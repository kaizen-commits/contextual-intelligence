import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from contextual_intelligence.config import Settings
from contextual_intelligence.models import PastePayload, PasteResult
from contextual_intelligence.ui.palette import PastePaletteWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class MockLlmClient:
    def __init__(self, tokens=None):
        self._tokens = tokens or ["TRANSFORMED ", "TEXT"]

    def stream_transform(self, payload):
        for t in self._tokens:
            yield t


def test_open_palette_rejects_non_text(qapp, monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: True,
    )
    settings = Settings()
    llm = MockLlmClient()
    palette = PastePaletteWindow(settings, llm)

    palette.open_palette("test.exe")
    assert not palette.instruction_input.isEnabled()
    assert not palette.copy_btn.isEnabled()
    assert "non-text content" in palette.status_label.text()
    palette.close()


def test_open_palette_rejects_empty_clipboard(qapp, monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "   ",
    )
    settings = Settings()
    llm = MockLlmClient()
    palette = PastePaletteWindow(settings, llm)

    palette.open_palette("test.exe")
    assert not palette.instruction_input.isEnabled()
    assert not palette.copy_btn.isEnabled()
    assert "empty" in palette.status_label.text()
    palette.close()


def test_open_palette_rejects_oversized_clipboard(qapp, monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "x" * 10000,
    )
    settings = Settings(max_paste_input_chars=8000)
    llm = MockLlmClient()
    palette = PastePaletteWindow(settings, llm)

    palette.open_palette("test.exe")
    assert not palette.instruction_input.isEnabled()
    assert not palette.copy_btn.isEnabled()
    assert "too long" in palette.status_label.text()
    palette.close()


def test_open_palette_valid_clipboard(qapp, monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "hello world",
    )
    settings = Settings()
    llm = MockLlmClient()
    palette = PastePaletteWindow(settings, llm)

    palette.open_palette("notepad.exe")
    assert palette.instruction_input.isEnabled()
    assert not palette.copy_btn.isEnabled()
    assert "Ready to transform" in palette.status_label.text()
    assert palette._source_app == "notepad.exe"
    palette.close()


def test_submit_and_stream_transformation(qapp, monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "hello world",
    )
    settings = Settings()
    llm = MockLlmClient(["BULLET 1", "\nBULLET 2"])
    palette = PastePaletteWindow(settings, llm)
    palette.open_palette("notepad.exe")

    palette.instruction_input.setText("summarize")
    palette._on_submit()

    # Wait for worker thread to complete
    if palette._worker:
        palette._worker.wait(2000)
    qapp.processEvents()

    assert palette.preview_edit.toPlainText() == "BULLET 1\nBULLET 2"
    assert palette.copy_btn.isEnabled()
    assert "Done in" in palette.status_label.text()
    palette.close()


def test_retrying_transform_clears_partial_preview(qapp):
    settings = Settings()
    llm = MockLlmClient()
    palette = PastePaletteWindow(settings, llm)
    palette.preview_edit.setPlainText("partial whitespace attempt")
    palette._current_result_text = "partial whitespace attempt"

    palette._on_retrying(2)

    assert palette.preview_edit.toPlainText() == ""
    assert palette._current_result_text == ""
    assert "retrying" in palette.status_label.text()
    palette.close()


def test_copy_failure_keeps_palette_open(qapp, monkeypatch):
    """Critical regression test: silent copy failure must NOT lose the user's result."""
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "hello world",
    )
    # Simulate clipboard locked by another app
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.write_text_clipboard",
        lambda text: False,
    )
    settings = Settings()
    llm = MockLlmClient(["RESULT TEXT"])
    palette = PastePaletteWindow(settings, llm)
    palette.open_palette("notepad.exe")

    palette.instruction_input.setText("summarize")
    palette._on_submit()
    if palette._worker:
        palette._worker.wait(2000)
    qapp.processEvents()

    assert palette.copy_btn.isEnabled()
    # Click copy
    palette._on_copy_clicked()

    # Palette must NOT close and must display an error
    assert palette.isVisible() or not palette.isHidden()
    assert "Copy failed: Clipboard locked" in palette.status_label.text()
    assert len(palette.history) == 0
    palette.close()


def test_copy_success_appends_to_history_and_closes(qapp, monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "hello world",
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.write_text_clipboard",
        lambda text: True,
    )
    settings = Settings()
    llm = MockLlmClient(["RESULT TEXT"])
    palette = PastePaletteWindow(settings, llm)
    palette.open_palette("notepad.exe")

    palette.instruction_input.setText("summarize")
    palette._on_submit()
    if palette._worker:
        palette._worker.wait(2000)
    qapp.processEvents()

    palette._on_copy_clicked()
    assert len(palette.history) == 1
    assert palette.history[0].transformed_text == "RESULT TEXT"
    palette.close()


def test_history_ring_cycling(qapp, monkeypatch):
    settings = Settings()
    llm = MockLlmClient()
    palette = PastePaletteWindow(settings, llm)

    # Populate history
    p1 = PastePayload(text="a", instruction="first instruction")
    p2 = PastePayload(text="b", instruction="second instruction")
    palette.history = [
        PasteResult(payload=p1, transformed_text="A"),
        PasteResult(payload=p2, transformed_text="B"),
    ]
    palette._history_idx = len(palette.history)

    palette.instruction_input.setFocus()
    # Press Up -> second instruction
    ev_up = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    palette.keyPressEvent(ev_up)
    assert palette.instruction_input.text() == "second instruction"

    # Press Up -> first instruction
    palette.keyPressEvent(ev_up)
    assert palette.instruction_input.text() == "first instruction"

    # Press Down -> second instruction
    ev_down = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    palette.keyPressEvent(ev_down)
    assert palette.instruction_input.text() == "second instruction"

    # Press Down -> clear (back to new input)
    palette.keyPressEvent(ev_down)
    assert palette.instruction_input.text() == ""
    palette.close()


def test_worker_signal_disconnection_and_cleanup(qapp, monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "hello world",
    )
    settings = Settings()
    llm = MockLlmClient(["RESULT TEXT"])
    palette = PastePaletteWindow(settings, llm)
    palette.open_palette("notepad.exe")

    palette.instruction_input.setText("summarize")
    palette._on_submit()
    old_worker = palette._worker
    assert old_worker is not None

    # Wait for old_worker to complete
    old_worker.wait(2000)
    qapp.processEvents()

    # Now cancel/cleanup worker (simulated by cancelling)
    palette.cancel_worker()
    assert palette._worker is None

    # Emitting a signal from old_worker after cleanup must NOT affect the UI
    old_worker.token_received.emit("zombie token")
    old_worker.error_occurred.emit("zombie error")
    qapp.processEvents()

    # The preview edit and status label should not have been updated by zombie signals
    assert "zombie token" not in palette.preview_edit.toPlainText()
    assert "zombie error" not in palette.status_label.text()
    palette.close()


def test_primary_button_states_send_and_copy(qapp, monkeypatch):
    """Verify primary button acts as Send before result and Copy after result (SCOPE-28)."""
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "hello world",
    )
    settings = Settings()
    llm = MockLlmClient(["TRANSFORMED"])
    palette = PastePaletteWindow(settings, llm)
    palette.open_palette("notepad.exe")

    # Initial state: Send button, disabled because input empty
    assert palette.copy_btn.text() == "Send"
    assert not palette.copy_btn.isEnabled()

    # Typing instruction enables Send button
    palette.instruction_input.setText("summarize")
    assert palette.copy_btn.text() == "Send"
    assert palette.copy_btn.isEnabled()

    # Clicking Send triggers submit
    palette._on_primary_btn_clicked()
    assert not palette.copy_btn.isEnabled()

    # Worker finishes -> button switches to Copy
    if palette._worker:
        palette._worker.wait(2000)
    qapp.processEvents()

    assert palette.copy_btn.text() == "Copy"
    assert palette.copy_btn.isEnabled()

    # Editing instruction after result switches button back to Send
    palette.instruction_input.setText("summarize differently")
    assert palette.copy_btn.text() == "Send"
    assert palette.copy_btn.isEnabled()
    palette.close()


def test_palette_dragging(qapp):
    settings = Settings()
    llm = MockLlmClient()
    palette = PastePaletteWindow(settings, llm)
    palette.show()
    initial_pos = palette.pos()

    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(initial_pos.x() + 10, initial_pos.y() + 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    palette.eventFilter(palette.card_frame, press_ev)
    assert palette._drag_pos is not None

    move_ev = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(60, 60),
        QPointF(initial_pos.x() + 60, initial_pos.y() + 60),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    palette.eventFilter(palette.card_frame, move_ev)
    assert palette.pos().x() == initial_pos.x() + 50
    assert palette.pos().y() == initial_pos.y() + 50

    release_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(60, 60),
        QPointF(initial_pos.x() + 60, initial_pos.y() + 60),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    palette.eventFilter(palette.card_frame, release_ev)
    assert palette._drag_pos is None
    palette.close()


def test_palette_copy_button_emits_copied_signal(qapp, monkeypatch):
    """Explicit Copy emits copied_from_palette for the lookup handoff (SCOPE-30)."""
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.write_text_clipboard", lambda text: True
    )
    settings = Settings()
    palette = PastePaletteWindow(settings, MockLlmClient())
    palette._current_result_text = "gadget"

    emitted = []
    palette.copied_from_palette.connect(emitted.append)
    palette._on_copy_clicked()
    assert emitted == ["gadget"]


def test_palette_preview_ctrl_c_emits_copied_signal(qapp):
    """Manual Ctrl+C over a preview selection emits copied_from_palette (SCOPE-30)."""
    settings = Settings()
    palette = PastePaletteWindow(settings, MockLlmClient())
    palette.preview_edit.setPlainText("gadget widget")
    cursor = palette.preview_edit.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(6, cursor.MoveMode.KeepAnchor)  # select "gadget"
    palette.preview_edit.setTextCursor(cursor)

    emitted = []
    palette.copied_from_palette.connect(emitted.append)
    copy_ev = QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier
    )
    handled = palette.eventFilter(palette.preview_edit, copy_ev)
    assert emitted == ["gadget"]
    assert handled is False  # QTextEdit still performs the actual copy
    palette.close()
