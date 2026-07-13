"""System tray application and global hotkey integration for Contextual Lookup and Smart Paste."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from contextual_intelligence.capture import CaptureOrchestrator, get_foreground_app_name
from contextual_intelligence.config import Settings
from contextual_intelligence.hotkey import (
    LOOKUP_HOTKEY_ID,
    PASTE_HOTKEY_ID,
    WM_QUIT,
    run_hotkey_loop,
)
from contextual_intelligence.instance import release_instance_lock
from contextual_intelligence.llm import LlmClient
from contextual_intelligence.models import MAX_LOOKUP_CHARS, RecentAppCopy
from contextual_intelligence.ui.palette import PastePaletteWindow
from contextual_intelligence.ui.popup import LookupPopupWindow
from contextual_intelligence.ui.worker import LookupWorker

log = logging.getLogger(__name__)

# Fixed shutdown grace before the watchdog hard-exits the process. Deliberately
# a constant — deriving it from request_timeout_s (up to 300s) would let quit
# appear hung for minutes, and a wedged cross-process UIA call never recovers
# no matter how long we wait.
SHUTDOWN_GRACE_S = 10.0


def _create_default_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#5e6ad2"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(14)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "C")
    painter.end()
    return QIcon(pixmap)


class HotkeyBridge(QObject):
    """Brings win32 RegisterHotKey messages from a daemon thread into the Qt event
    loop safely via signals.

    Shutdown handshake: the loop thread signals `_ready` once its message queue
    exists (so WM_QUIT can actually be posted to it) and `_done` when the loop
    has exited. `stop()` reports whether the thread really terminated — the
    tray blocks normal Qt teardown on a False result.
    """

    hotkey_pressed = Signal()  # legacy signal for lookup
    lookup_triggered = Signal()
    paste_triggered = Signal(str)  # source app name
    registration_failed = Signal(int, int)  # hotkey_id, vk
    stopped = Signal()  # loop thread has fully exited (emitted from its finally)

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self.thread: threading.Thread | None = None
        self.thread_id: int | None = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._stopping = threading.Event()

    def start(self, hotkey_map: dict[int, tuple[int, Callable[[], None]]] | None = None) -> None:
        self.thread = threading.Thread(
            target=self._loop, args=(hotkey_map,), daemon=True, name="hotkey-loop"
        )
        self.thread.start()

    def _loop(self, hotkey_map: dict[int, tuple[int, Callable[[], None]]] | None) -> None:
        def _set_thread_id(tid: int) -> None:
            self.thread_id = tid

        def _on_fail(hid: int, vk: int) -> None:
            self.registration_failed.emit(hid, vk)

        if hotkey_map is None:
            hotkey_map = {
                LOOKUP_HOTKEY_ID: (
                    ord("D"),
                    lambda: (self.hotkey_pressed.emit(), self.lookup_triggered.emit()),
                ),
            }

        try:
            run_hotkey_loop(
                hotkey_map=hotkey_map,
                on_thread_id=_set_thread_id,
                on_registration_failure=_on_fail,
                on_ready=self._ready.set,
                stopping=self._stopping,
            )
        except Exception as exc:
            # HotkeyError messages are our own static strings; anything else
            # is foreign text that stays out of the logs (class name only).
            from contextual_intelligence.hotkey import HotkeyError

            if isinstance(exc, HotkeyError):
                log.error("hotkey loop stopped: %s", exc)
            else:
                log.error("hotkey loop stopped (%s)", type(exc).__name__)
        finally:
            self._done.set()
            # Queued to the GUI thread: lets a gated shutdown resume when the
            # thread outlives stop()'s bounded waits instead of riding the
            # watchdog into a hard exit.
            self.stopped.emit()

    def stop(self) -> bool:
        """Stop the message loop. Returns True when the thread has terminated."""
        self._stopping.set()
        if self.thread is None:
            return True
        if self._ready.wait(2.0) and self.thread_id is not None:
            import ctypes

            posted = ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            if not posted:
                log.warning(
                    "PostThreadMessageW(WM_QUIT) failed for hotkey thread %s", self.thread_id
                )
        self.thread.join(2.0)
        self._done.wait(0.5)
        if self.thread.is_alive():
            log.warning("hotkey thread did not terminate; deferring to the shutdown watchdog")
            return False
        return True


class TrayApplication(QObject):
    """Manages the QApplication, system tray icon, popup window, palette, and hotkey wiring."""

    def __init__(
        self,
        settings: Settings,
        orchestrator: CaptureOrchestrator,
        llm_client: LlmClient,
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.orchestrator = orchestrator
        self.llm_client = llm_client

        self._quitting = False
        self._teardown_done = False
        self._watchdog: threading.Timer | None = None
        self._hotkey_stopped = True
        self._gated_worker_ids: set[int] = set()
        self._failed_hotkeys: set[int] = set()

        self.app = QApplication.instance()
        if self.app is None:
            self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.popup = LookupPopupWindow()
        self.paste_palette = PastePaletteWindow(settings, llm_client)
        self._recent_app_copy: RecentAppCopy | None = None
        self.paste_palette.copied_from_palette.connect(self._record_app_copy)
        self.tray_icon = QSystemTrayIcon(_create_default_icon(), self.app)
        self.tray_icon.setToolTip("Contextual Intelligence (Ctrl+Alt+D / Ctrl+Alt+V)")

        self._setup_menu()
        self.tray_icon.show()

        self.hotkey_bridge = HotkeyBridge(self)
        self.hotkey_bridge.lookup_triggered.connect(self.trigger_lookup)
        self.hotkey_bridge.paste_triggered.connect(self.trigger_paste)
        self.hotkey_bridge.registration_failed.connect(self._on_hotkey_failed)
        self.hotkey_bridge.stopped.connect(self._on_hotkey_thread_stopped)

        hotkey_map = {
            LOOKUP_HOTKEY_ID: (
                0x44,  # ord('D')
                lambda: (
                    self.hotkey_bridge.hotkey_pressed.emit(),
                    self.hotkey_bridge.lookup_triggered.emit(),
                ),
            ),
            PASTE_HOTKEY_ID: (
                self.settings.paste_hotkey_vk,
                lambda: self.hotkey_bridge.paste_triggered.emit(get_foreground_app_name()),
            ),
        }
        self.hotkey_bridge.start(hotkey_map)
        # No UIA warm-up thread: the ~1s first-lookup latency it saved is not
        # worth the COM-init-vs-Qt-teardown race class it created (hardening
        # pass, Rev 3 Slice C).

    def _setup_menu(self) -> None:
        menu = QMenu()
        quit_action = menu.addAction("Quit Contextual Intelligence")
        quit_action.triggered.connect(self.quit)

        self.tray_icon.setContextMenu(menu)

    def _on_hotkey_failed(self, hid: int, vk: int) -> None:
        name = "Contextual Lookup" if hid == LOOKUP_HOTKEY_ID else "Smart Paste"
        char_rep = chr(vk) if 32 <= vk <= 126 else f"0x{vk:x}"
        log.warning(
            "Failed to register shortcut for %s (Ctrl+Alt+%s) — another app may own it.",
            name,
            char_rep,
        )
        # A log line is invisible in a tray app: surface the degradation where
        # the user can see it, and keep the tooltip truthful about what works.
        self._failed_hotkeys.add(hid)
        if {LOOKUP_HOTKEY_ID, PASTE_HOTKEY_ID} <= self._failed_hotkeys:
            self.tray_icon.showMessage(
                "Shortcut unavailable",
                "No shortcuts could be registered — Contextual Intelligence is running "
                "but idle. Quit, free Ctrl+Alt+D / Ctrl+Alt+V, and restart.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
            self.tray_icon.setToolTip("Contextual Intelligence (hotkeys unavailable)")
        else:
            self.tray_icon.showMessage(
                "Shortcut unavailable",
                f"Ctrl+Alt+{char_rep} ({name}) is owned by another app — that feature "
                "is disabled until it is freed.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
            working = "Ctrl+Alt+V" if hid == LOOKUP_HOTKEY_ID else "Ctrl+Alt+D"
            self.tray_icon.setToolTip(f"Contextual Intelligence ({working})")

    def _record_app_copy(self, text: str) -> None:
        """Remember short text copied out of the Smart Paste palette so a
        follow-up Lookup can use it after all capture tiers fail (SCOPE-30)."""
        text = text.strip()
        if not text or len(text) > MAX_LOOKUP_CHARS:
            log.debug("ignoring palette copy for lookup handoff (%d chars)", len(text))
            return
        self._recent_app_copy = RecentAppCopy(
            text=text, copied_at=time.monotonic(), source="smart_paste"
        )
        log.info("recorded smart_paste copy for lookup handoff (%d chars)", len(text))

    def trigger_lookup(self) -> None:
        if self._quitting:
            return
        log.info("triggering contextual lookup")
        delay_ms = 0
        palette_was_visible = self.paste_palette.isVisible()
        if palette_was_visible:
            log.info("closing open Smart Paste palette before triggering lookup")
            self.paste_palette.close()
            delay_ms = 150  # Allow Windows OS time to restore foreground focus to the target app

        def _start() -> None:
            if self._quitting:
                return
            worker = LookupWorker(
                self.orchestrator,
                self.llm_client,
                parent=self,
                recent_copy=self._recent_app_copy,
                # Looking up from the palette: the source app's leftover
                # selection is stale, so a valid palette copy takes priority.
                prefer_recent_copy=palette_was_visible,
                fallback_enabled=self.settings.enable_clipboard_fallback,
            )
            self.popup.start_lookup(worker)

        if delay_ms > 0:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(delay_ms, _start)
        else:
            _start()

    def trigger_paste(self, source_app: str = "") -> None:
        if self._quitting:
            return
        log.info("triggering smart paste palette (source app: %s)", source_app or "unknown")
        if self.popup.isVisible():
            log.info("closing open Contextual Lookup popup before triggering paste")
            self.popup.close()
        self.paste_palette.open_palette(source_app)

    def run(self) -> int:
        log.info("starting tray app event loop")
        import signal
        from PySide6.QtCore import QTimer

        signal.signal(signal.SIGINT, lambda *args: self.quit())

        timer = QTimer(self.app)
        timer.timeout.connect(lambda: None)
        timer.start(200)

        return self.app.exec()

    def quit(self) -> None:
        """Idempotent shutdown. Invariant: no Qt object is destroyed while any
        owned worker (or the hotkey thread) is still running — teardown is
        gated on their exit, and the watchdog hard-exits (skipping all
        destructors) if they never do."""
        if self._quitting:
            return
        self._quitting = True
        log.info("quitting tray app")

        # Armed before anything can block; an independent daemon timer because
        # a QTimer needs the (possibly blocked) GUI event loop to fire.
        self._watchdog = threading.Timer(SHUTDOWN_GRACE_S, self._on_shutdown_watchdog)
        self._watchdog.daemon = True
        self._watchdog.start()

        self._hotkey_stopped = bool(self.hotkey_bridge.stop())

        # Cooperative cancel first, then close the LLM client to abort any
        # stream blocked in network I/O (acceleration, not proof: it cannot
        # unblock a wedged cross-process UIA capture).
        self.popup.request_cancel()
        self.paste_palette.request_cancel()
        self.llm_client.close()

        # Bounded waits keep the common case synchronous; a worker that
        # outlives them is retained and gates teardown via its finished signal.
        if not self.popup.cancel_lookup(2000):
            log.warning("lookup worker did not stop within 2 seconds; gating teardown on it")
        if not self.paste_palette.cancel_worker(2000):
            log.warning("paste worker did not stop within 2 seconds; gating teardown on it")

        self._try_finish_quit()

    def _live_shutdown_blockers(self) -> list[Any]:
        return [
            w
            for w in (*self.popup.live_workers(), *self.paste_palette.live_workers())
            if w.isRunning()
        ]

    def _on_hotkey_thread_stopped(self) -> None:
        """Delayed completion of a hotkey thread that outlived stop()'s bounded
        waits — resume the gated shutdown instead of leaving it to the watchdog."""
        self._hotkey_stopped = True
        if self._quitting:
            self._try_finish_quit()

    def _try_finish_quit(self) -> None:
        if self._teardown_done or not self._quitting:
            return
        blockers = self._live_shutdown_blockers()
        if blockers or not self._hotkey_stopped:
            for worker in blockers:
                if id(worker) not in self._gated_worker_ids:
                    self._gated_worker_ids.add(id(worker))
                    worker.finished.connect(self._try_finish_quit)
            log.warning(
                "shutdown gated on %d running worker(s)%s; watchdog in %.0fs",
                len(blockers),
                "" if self._hotkey_stopped else " and the hotkey thread",
                SHUTDOWN_GRACE_S,
            )
            return
        self._teardown_done = True
        self.popup.close()
        self.paste_palette.close()
        self.tray_icon.hide()
        release_instance_lock()
        if self._watchdog is not None:
            self._watchdog.cancel()
        self.app.quit()

    def _on_shutdown_watchdog(self) -> None:
        names = ", ".join(t.name for t in threading.enumerate())
        log.critical(
            "shutdown watchdog fired after %.0fs; live threads: %s — hard exit without "
            "running destructors",
            SHUTDOWN_GRACE_S,
            names,
        )
        os._exit(1)
