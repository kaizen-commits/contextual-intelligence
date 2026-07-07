"""Frameless near-cursor popup window for Contextual Lookup."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from contextual_intelligence.models import ContextPayload
from contextual_intelligence.ui.positioning import clamp_to_screen, position_near_cursor
from contextual_intelligence.ui.worker import LookupWorker

log = logging.getLogger(__name__)

STYLESHEET = """
QFrame#CardFrame {
    background-color: #18181b;
    border: 1px solid #3f3f46;
    border-radius: 8px;
}
QLabel {
    font-family: "Segoe UI", "Inter", sans-serif;
}
QLabel#TitleLabel {
    color: #818cf8;
    font-size: 13pt;
    font-weight: bold;
}
QLabel#DefLabel {
    color: #f4f4f5;
    font-size: 11pt;
}
QLabel#CtxLabel {
    color: #d4d4d8;
    font-size: 10pt;
    font-style: italic;
}
QLabel#SynLabel {
    color: #a1a1aa;
    font-size: 10pt;
}
QLabel#StatusLabel {
    color: #a1a1aa;
    font-size: 11pt;
}
QPushButton#CloseBtn {
    background: transparent;
    color: #a1a1aa;
    border: none;
    font-size: 13pt;
    font-weight: bold;
    padding: 2px 6px;
}
QPushButton#CloseBtn:hover {
    color: #ef4444;
}
"""


class LookupPopupWindow(QWidget):
    """Compact dictionary card overlay matching the recovered recording shape:
    title `{term} (part of speech)`, concise definition, context domain line,
    and synonyms."""

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(STYLESHEET)
        self.resize(400, 150)
        self.setMinimumWidth(380)
        self.setMaximumWidth(480)

        self._worker: LookupWorker | None = None
        self._buffer: str = ""
        self._drag_pos = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.card_frame = QFrame(self)
        self.card_frame.setObjectName("CardFrame")
        main_layout.addWidget(self.card_frame)

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(14, 12, 14, 14)
        card_layout.setSpacing(8)

        # Top bar: status/header + close button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("⏳ Analyzing context...", self.card_frame)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        top_bar.addWidget(self.status_label, 1)

        self.close_btn = QPushButton("✕", self.card_frame)
        self.close_btn.setObjectName("CloseBtn")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        top_bar.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(top_bar)

        # Content labels (each line of the 4-line dictionary shape)
        self.title_label = QLabel("", self.card_frame)
        self.title_label.setObjectName("TitleLabel")
        self.title_label.setWordWrap(True)
        self.title_label.hide()
        card_layout.addWidget(self.title_label)

        self.def_label = QLabel("", self.card_frame)
        self.def_label.setObjectName("DefLabel")
        self.def_label.setWordWrap(True)
        self.def_label.hide()
        card_layout.addWidget(self.def_label)

        self.ctx_label = QLabel("", self.card_frame)
        self.ctx_label.setObjectName("CtxLabel")
        self.ctx_label.setWordWrap(True)
        self.ctx_label.hide()
        card_layout.addWidget(self.ctx_label)

        self.syn_label = QLabel("", self.card_frame)
        self.syn_label.setObjectName("SynLabel")
        self.syn_label.setWordWrap(True)
        self.syn_label.hide()
        card_layout.addWidget(self.syn_label)

    def start_lookup(self, worker: LookupWorker) -> None:
        if self._worker is not None and self._worker.isRunning():
            log.warning("Lookup already in progress, ignoring trigger")
            worker.deleteLater()
            return

        if self._worker is not None:
            # Disconnect all signals from the old worker to prevent late callbacks from clobbering the UI
            try:
                self._worker.disconnect(self)
            except Exception:
                pass
            # Schedule safe deletion or delete immediately if already finished
            if self._worker.isFinished():
                self._worker.deleteLater()
            else:
                self._worker.finished.connect(self._worker.deleteLater)

        self._worker = worker
        self._worker.started_capture.connect(self._on_started)
        self._worker.capture_succeeded.connect(self._on_capture_succeeded)
        self._worker.token_received.connect(self._on_token)
        self._worker.finished_lookup.connect(self._on_finished)
        self._worker.error_occurred.connect(self._on_error)

        position_near_cursor(self)
        self.show()
        self._worker.start()

    def cancel_lookup(self, timeout_ms: int = 2000) -> bool:
        """Cancel the active worker and wait for it to exit (bounded wait)."""
        if self._worker is not None and self._worker.isRunning():
            log.info("cancelling active worker thread")
            self._worker.cancel()
            return self._worker.wait(timeout_ms)
        return True

    def _on_started(self) -> None:
        self._buffer = ""
        self.title_label.hide()
        self.def_label.hide()
        self.ctx_label.hide()
        self.syn_label.hide()
        self.status_label.setText("⏳ Analyzing context...")
        self.status_label.show()
        self.adjustSize()
        clamp_to_screen(self)

    def _on_capture_succeeded(self, payload: ContextPayload) -> None:
        app = payload.app_name or "unknown app"
        self.status_label.setText(f"⏳ Defining '{payload.selected_text}' ({app})...")
        self.adjustSize()
        clamp_to_screen(self)

    def _on_token(self, chunk: str) -> None:
        self._buffer += chunk
        lines = [line.strip() for line in self._buffer.split("\n") if line.strip()]

        # Hide loading status only once visible content has arrived; a
        # whitespace-only stream must not blank the card.
        if lines:
            self.status_label.hide()

        if len(lines) >= 1:
            self.title_label.setText(lines[0])
            self.title_label.show()
        if len(lines) >= 2:
            self.def_label.setText(lines[1])
            self.def_label.show()
        if len(lines) >= 3:
            self.ctx_label.setText(lines[2])
            self.ctx_label.show()
        if len(lines) >= 4:
            self.syn_label.setText("\n".join(lines[3:]))
            self.syn_label.show()

        self.adjustSize()
        clamp_to_screen(self)

    def _on_finished(self) -> None:
        if self._buffer.strip():
            self.status_label.hide()
        else:
            log.warning("lookup finished with empty model response")
            self.status_label.setText(
                "❌ Model returned an empty response — re-select the word and try again"
            )
            self.status_label.show()
        self.adjustSize()
        clamp_to_screen(self)

    def _on_error(self, msg: str) -> None:
        self.title_label.hide()
        self.def_label.hide()
        self.ctx_label.hide()
        self.syn_label.hide()
        self.status_label.setText(f"❌ {msg}")
        self.status_label.show()
        self.adjustSize()
        clamp_to_screen(self)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def closeEvent(self, event: Any) -> None:
        self.cancel_lookup()
        super().closeEvent(event)
