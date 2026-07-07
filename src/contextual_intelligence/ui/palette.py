"""Smart Paste UI Palette window.

Provides an interactive command palette overlay that activates on hotkey,
inspects clipboard content safely, streams transformation from LM Studio,
and allows explicit copy-first results without automatic paste-back.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from contextual_intelligence.clipboard import (
    has_high_value_non_text_format,
    read_text_clipboard,
    write_text_clipboard,
)
from contextual_intelligence.config import Settings
from contextual_intelligence.llm import LlmClient
from contextual_intelligence.models import PastePayload, PasteResult
from contextual_intelligence.ui.paste_worker import PasteWorker
from contextual_intelligence.ui.positioning import clamp_to_screen, position_near_cursor

log = logging.getLogger(__name__)

STYLESHEET = """
QFrame#CardFrame {
    background-color: #18181b;
    border: 1px solid #3f3f46;
    border-radius: 8px;
}
QLabel {
    font-family: "Segoe UI", "Inter", sans-serif;
    color: #f4f4f5;
}
QLabel#HeaderLabel {
    color: #818cf8;
    font-size: 11pt;
    font-weight: bold;
}
QLabel#StatusLabel {
    color: #a1a1aa;
    font-size: 10pt;
}
QLineEdit#InstructionInput {
    background-color: #27272a;
    border: 1px solid #52525b;
    border-radius: 6px;
    color: #f4f4f5;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 11pt;
    padding: 6px 10px;
}
QLineEdit#InstructionInput:focus {
    border: 1px solid #818cf8;
}
QTextEdit#PreviewEdit {
    background-color: #27272a;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    color: #e4e4e7;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 10pt;
    padding: 6px;
}
QPushButton {
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
    font-weight: bold;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton#CopyBtn {
    background-color: #6366f1;
    color: #ffffff;
    border: none;
}
QPushButton#CopyBtn:hover {
    background-color: #4f46e5;
}
QPushButton#CopyBtn:disabled {
    background-color: #3f3f46;
    color: #71717a;
}
QPushButton#CancelBtn {
    background-color: transparent;
    color: #a1a1aa;
    border: 1px solid #3f3f46;
}
QPushButton#CancelBtn:hover {
    background-color: #27272a;
    color: #f4f4f5;
}
"""


def _force_foreground(window: QWidget) -> None:
    window.raise_()
    window.activateWindow()
    try:
        import ctypes

        hwnd = int(window.winId())
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


class PastePaletteWindow(QWidget):
    """Interactive Smart Paste palette overlay."""

    def __init__(
        self,
        settings: Settings,
        llm_client: LlmClient,
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._llm_client = llm_client

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLESHEET)
        self.resize(500, 320)
        self.setMinimumWidth(400)
        self.setMaximumWidth(650)

        self.history: list[PasteResult] = []
        self._worker: PasteWorker | None = None
        self._clipboard_text: str = ""
        self._source_app: str = ""
        self._current_result_text: str = ""
        self._current_payload: PastePayload | None = None
        self._current_duration_ms: float = 0.0
        self._history_idx: int = -1

        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.card_frame = QFrame(self)
        self.card_frame.setObjectName("CardFrame")
        main_layout.addWidget(self.card_frame)

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(10)

        # Header bar
        header_layout = QHBoxLayout()
        self.header_label = QLabel("✨ Smart Paste", self.card_frame)
        self.header_label.setObjectName("HeaderLabel")
        header_layout.addWidget(self.header_label)
        header_layout.addStretch()
        card_layout.addLayout(header_layout)

        # Instruction input
        self.instruction_input = QLineEdit(self.card_frame)
        self.instruction_input.setObjectName("InstructionInput")
        self.instruction_input.setPlaceholderText(
            "e.g., summarize in bullet points, convert to JSON..."
        )
        self.instruction_input.returnPressed.connect(self._on_submit)
        card_layout.addWidget(self.instruction_input)

        # Status label
        self.status_label = QLabel("Ready", self.card_frame)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)

        # Preview area
        self.preview_edit = QTextEdit(self.card_frame)
        self.preview_edit.setObjectName("PreviewEdit")
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setPlaceholderText("Transformation preview will appear here...")
        card_layout.addWidget(self.preview_edit)

        # Button bar
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel", self.card_frame)
        self.cancel_btn.setObjectName("CancelBtn")
        self.cancel_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.cancel_btn)

        self.copy_btn = QPushButton("Copy", self.card_frame)
        self.copy_btn.setObjectName("CopyBtn")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        btn_layout.addWidget(self.copy_btn)

        card_layout.addLayout(btn_layout)

    def open_palette(self, source_app: str = "") -> None:
        """Inspect clipboard and open palette if valid."""
        self.cancel_worker()
        self._source_app = source_app
        self._current_result_text = ""
        self._current_payload = None
        self._current_duration_ms = 0.0
        self._history_idx = len(self.history)
        self.preview_edit.clear()
        self.instruction_input.clear()
        self.copy_btn.setEnabled(False)

        # 1. Non-text check (fail closed)
        if has_high_value_non_text_format():
            self._show_error(
                "❌ Clipboard contains non-text content (image/file). Please copy text first."
            )
            self._present_window()
            return

        # 2. Read text
        text = read_text_clipboard()
        if not text or not text.strip():
            self._show_error("❌ Clipboard is empty. Please copy some text first.")
            self._present_window()
            return

        # 3. Length check (reject at open/validate, do not truncate)
        max_chars = self._settings.max_paste_input_chars
        if len(text) > max_chars:
            self._show_error(
                f"❌ Clipboard text too long ({len(text):,} chars > {max_chars:,} limit)."
            )
            self._present_window()
            return

        self._clipboard_text = text
        self.instruction_input.setEnabled(True)
        app_msg = f" from {source_app}" if source_app else ""
        self.status_label.setText(f"Ready to transform {len(text):,} chars{app_msg}.")
        self.status_label.show()

        self._present_window()

    def _show_error(self, msg: str) -> None:
        self.status_label.setText(msg)
        self.status_label.show()
        self.instruction_input.setEnabled(False)
        self.copy_btn.setEnabled(False)

    def _present_window(self) -> None:
        position_near_cursor(self)
        self.show()
        _force_foreground(self)
        if self.instruction_input.isEnabled():
            self.instruction_input.setFocus()

    def cancel_worker(self, timeout_ms: int = 2000) -> bool:
        if self._worker is not None and self._worker.isRunning():
            log.info("cancelling active paste worker thread")
            self._worker.cancel()
            res = self._worker.wait(timeout_ms)
            self._worker = None
            return res
        self._worker = None
        return True

    def _on_submit(self) -> None:
        instruction = self.instruction_input.text().strip()
        if not instruction:
            return
        if not self._clipboard_text:
            return

        self.cancel_worker()
        self.copy_btn.setEnabled(False)
        self.preview_edit.clear()
        self.status_label.setText("⏳ Transforming...")
        self._current_result_text = ""

        try:
            payload = PastePayload(
                text=self._clipboard_text,
                instruction=instruction,
                app_name=self._source_app,
            )
        except Exception as exc:
            self.status_label.setText(f"❌ Invalid request: {exc}")
            return

        self._current_payload = payload
        worker = PasteWorker(payload, self._llm_client)
        worker.started_transform.connect(self._on_started)
        worker.retrying_transform.connect(self._on_retrying)
        worker.token_received.connect(self._on_token)
        worker.finished_transform.connect(self._on_finished)
        worker.error_occurred.connect(self._on_error)
        self._worker = worker
        worker.start()

    def _on_started(self) -> None:
        self.status_label.setText("⏳ Streaming transformation from LM Studio...")

    def _on_retrying(self, attempt: int) -> None:
        self._current_result_text = ""
        self.preview_edit.clear()
        self.copy_btn.setEnabled(False)
        self.status_label.setText(f"⏳ Empty response from model, retrying (attempt {attempt})...")

    def _on_token(self, chunk: str) -> None:
        self._current_result_text += chunk
        # Use insertPlainText or setPlainText for smooth preview updates
        self.preview_edit.setPlainText(self._current_result_text)
        self.preview_edit.verticalScrollBar().setValue(
            self.preview_edit.verticalScrollBar().maximum()
        )
        clamp_to_screen(self)

    def _on_finished(self, transformed_text: str, duration_ms: float) -> None:
        self._current_result_text = transformed_text
        self._current_duration_ms = duration_ms
        self.preview_edit.setPlainText(transformed_text)
        if transformed_text:
            self.status_label.setText(
                f"✅ Done in {duration_ms:.0f}ms. Review preview and click Copy."
            )
            self.copy_btn.setEnabled(True)
            self.copy_btn.setFocus()
        else:
            self.status_label.setText("❌ Model returned empty transformation.")
        clamp_to_screen(self)

    def _on_error(self, msg: str) -> None:
        self.status_label.setText(f"❌ {msg}")
        self.copy_btn.setEnabled(False)
        clamp_to_screen(self)

    def _on_copy_clicked(self) -> None:
        if not self._current_result_text:
            return
        success = write_text_clipboard(self._current_result_text)
        if success:
            if self._current_payload:
                res = PasteResult(
                    payload=self._current_payload,
                    transformed_text=self._current_result_text,
                    duration_ms=self._current_duration_ms,
                )
                self.history.append(res)
                if len(self.history) > 20:
                    self.history.pop(0)
            self.close()
        else:
            log.error("copy to clipboard failed from palette")
            self.status_label.setText(
                "❌ Copy failed: Clipboard locked by another app. Try again."
            )

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_worker()
            self.close()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Up:
            if self.history and self._history_idx > 0:
                self._history_idx -= 1
                self.instruction_input.setText(self.history[self._history_idx].payload.instruction)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Down:
            if self.history and self._history_idx < len(self.history) - 1:
                self._history_idx += 1
                self.instruction_input.setText(self.history[self._history_idx].payload.instruction)
            elif self._history_idx == len(self.history) - 1:
                self._history_idx = len(self.history)
                self.instruction_input.clear()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: Any) -> None:
        self.cancel_worker()
        super().closeEvent(event)
