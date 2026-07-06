"""System tray application and global hotkey integration for Contextual Lookup."""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from contextual_intelligence.capture import CaptureOrchestrator
from contextual_intelligence.config import Settings
from contextual_intelligence.hotkey import run_hotkey_loop
from contextual_intelligence.llm import LlmClient
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

    hotkey_pressed = Signal()

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self.thread: threading.Thread | None = None
        self.thread_id: int | None = None

    def start(self, vk: int = ord("D")) -> None:
        self.thread = threading.Thread(target=self._loop, args=(vk,), daemon=True)
        self.thread.start()

    def _loop(self, vk: int) -> None:
        def _set_thread_id(tid: int) -> None:
            self.thread_id = tid

        try:
            run_hotkey_loop(
                lambda: self.hotkey_pressed.emit(),
                vk=vk,
                on_thread_id=_set_thread_id,
            )
        except Exception as exc:
            log.error("hotkey loop stopped: %s", exc)

    def stop(self) -> None:
        if self.thread_id is not None:
            import ctypes
            # Post WM_QUIT (0x0012) to the message loop running on the hotkey thread
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread is not None:
            self.thread.join(1.0)


class TrayApplication(QObject):
    """Manages the QApplication, system tray icon, popup window, and hotkey wiring."""

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
        self.tray_icon = QSystemTrayIcon(_create_default_icon(), self.app)
        self.tray_icon.setToolTip("Contextual Intelligence (Ctrl+Alt+D)")

        self._setup_menu()
        self.tray_icon.show()

        self.hotkey_bridge = HotkeyBridge(self)
        self.hotkey_bridge.hotkey_pressed.connect(self.trigger_lookup)
        self.hotkey_bridge.start()

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

    def trigger_lookup(self) -> None:
        log.info("triggering contextual lookup")
        worker = LookupWorker(self.orchestrator, self.llm_client, parent=self)
        self.popup.start_lookup(worker)

    def run(self) -> int:
        log.info("starting tray app event loop")
        import signal
        from PySide6.QtCore import QTimer

        # Ensure Ctrl+C in terminal triggers clean Qt app shutdown
        signal.signal(signal.SIGINT, lambda *args: self.quit())

        # Qt's C++ event loop blocks Python signal handlers unless Python bytecode is
        # executed periodically. A periodic timer wakes Python up to check signals.
        timer = QTimer(self.app)
        timer.timeout.connect(lambda: None)
        timer.start(200)

        return self.app.exec()

    def quit(self) -> None:
        log.info("quitting tray app")
        # 1. Stop hotkey loop
        self.hotkey_bridge.stop()

        # 2. Cancel and wait for a live worker (bounded wait)
        if not self.popup.cancel_lookup(2000):
            log.warning("worker thread did not stop within 2 seconds; proceeding anyway")

        self.popup.close()
        self.tray_icon.hide()
        self.app.quit()
