import pytest

from contextual_intelligence.capture import clipboard_fallback
from contextual_intelligence.capture.clipboard_fallback import ClipboardFallbackProvider
from contextual_intelligence.models import CaptureError, CaptureTier


def test_clipboard_capture_success(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    clipboard_state = ["original clipboard"]
    restored = []

    def mock_save():
        return clipboard_state[-1]

    def mock_restore(text):
        restored.append(text)

    def mock_send_ctrl_c():
        clipboard_state.append("newly copied selection")

    seq = [100]
    def mock_get_seq():
        return seq[-1]

    def mock_send_ctrl_c_with_seq():
        clipboard_state.append("newly copied selection")
        seq.append(101)

    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", mock_save)
    monkeypatch.setattr(clipboard_fallback, "_restore_clipboard", mock_restore)
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", mock_send_ctrl_c_with_seq)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", mock_get_seq)

    payload = provider.capture()
    assert payload.selected_text == "newly copied selection"
    assert payload.tier == CaptureTier.CLIPBOARD
    assert restored == ["original clipboard"]


def test_clipboard_capture_empty_raises_error(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    restored = []
    seq = [100]
    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", lambda: "")
    monkeypatch.setattr(clipboard_fallback, "_restore_clipboard", lambda text: restored.append(text))
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: seq.append(101))
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: seq[-1])

    with pytest.raises(CaptureError, match="no text or empty selection"):
        provider.capture()

    # Verify clipboard was restored even when capture raised an error
    assert len(restored) == 1


def test_clipboard_sequence_not_incrementing_raises_error(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    restored = []
    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", lambda: "stale text")
    monkeypatch.setattr(clipboard_fallback, "_restore_clipboard", lambda text: restored.append(text))
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: None)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: 100)

    with pytest.raises(CaptureError, match="sequence number did not increment"):
        provider.capture()

    assert len(restored) == 1
