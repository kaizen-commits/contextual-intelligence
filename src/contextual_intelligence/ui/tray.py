"""System tray application and global hotkey integration for Contextual Lookup and Smart Paste."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from contextual_intelligence.capture import CaptureOrchestrator, get_foreground_app_name
from contextual_intelligence.config import Settings
from contextual_intelligence.hotkey import LOOKUP_HOTKEY_ID, PASTE_HOTKEY_ID, run_hotkey_loop
from contextual_intelligence.llm import LlmClient
from contextual_intelligence.models import MAX_LOOKUP_CHARS, RecentAppCopy
from contextual_intelligence.ui.palette import PastePaletteWindow
from contextual_intelligence.ui.popup import LookupPopupWindow
from contextual_intelligence.ui.worker import LookupWorker

log = logging.getLogger(__name__)


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
    loop safely via signals."""

    hotkey_pressed = Signal()  # legacy signal for lookup
    lookup_triggered = Signal()
    paste_triggered = Signal(str)  # source app name
    registration_failed = Signal(int, int)  # hotkey_id, vk

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self.thread: threading.Thread | None = None
        self.thread_id: int | None = None

    def start(self, hotkey_map: dict[int, tuple[int, Callable[[], None]]] | None = None) -> None:
        self.thread = threading.Thread(target=self._loop, args=(hotkey_map,), daemon=True)
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
            )
        except Exception as exc:
            log.error("hotkey loop stopped: %s", exc)

    def stop(self) -> None:
        if self.thread_id is not None:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread is not None:
            self.thread.join(1.0)


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

        # Warm up UI Automation in a background thread to avoid cold startup latency (~2s)
        threading.Thread(target=self._warmup_uia, daemon=True).start()

    def _warmup_uia(self) -> None:
        try:
            import uiautomation as auto

            with auto.UIAutomationInitializerInThread(debug=False):
                auto.GetFocusedControl()
            log.info("UIA warmed up successfully")
        except Exception as exc:
            log.debug("UIA warm-up failed: %s", exc)

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
        log.info("triggering contextual lookup")
        delay_ms = 0
        palette_was_visible = self.paste_palette.isVisible()
        if palette_was_visible:
            log.info("closing open Smart Paste palette before triggering lookup")
            self.paste_palette.close()
            delay_ms = 150  # Allow Windows OS time to restore foreground focus to the target app

        def _start() -> None:
            worker = LookupWorker(
                self.orchestrator,
                self.llm_client,
                parent=self,
                recent_copy=self._recent_app_copy,
                # Looking up from the palette: the source app's leftover
                # selection is stale, so a valid palette copy takes priority.
                prefer_recent_copy=palette_was_visible,
            )
            self.popup.start_lookup(worker)

        if delay_ms > 0:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(delay_ms, _start)
        else:
            _start()

    def trigger_paste(self, source_app: str = "") -> None:
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
        log.info("quitting tray app")
        self.hotkey_bridge.stop()

        if not self.popup.cancel_lookup(2000):
            log.warning("lookup worker thread did not stop within 2 seconds; proceeding anyway")

        self.paste_palette.cancel_worker(2000)

        self.popup.close()
        self.paste_palette.close()
        self.tray_icon.hide()
        self.app.quit()
