import win32con
import ctypes

from contextual_intelligence import clipboard
from contextual_intelligence.models import SnapshotStatus, RestoreOutcome


def test_has_high_value_non_text_format_true(monkeypatch):
    formats = [win32con.CF_UNICODETEXT, win32con.CF_BITMAP, 0]
    fmt_iter = iter(formats)

    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)
    monkeypatch.setattr(
        clipboard.win32clipboard, "EnumClipboardFormats", lambda _: next(fmt_iter)
    )

    assert clipboard.has_high_value_non_text_format() is True


def test_has_high_value_non_text_format_false(monkeypatch):
    formats = [win32con.CF_UNICODETEXT, 0]
    fmt_iter = iter(formats)

    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)
    monkeypatch.setattr(
        clipboard.win32clipboard, "EnumClipboardFormats", lambda _: next(fmt_iter)
    )

    assert clipboard.has_high_value_non_text_format() is False


def test_has_high_value_non_text_format_fail_closed(monkeypatch):
    def mock_open():
        raise RuntimeError("clipboard locked")

    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", mock_open)
    # Fast retry in tests
    monkeypatch.setattr(clipboard.time, "sleep", lambda _: None)

    assert clipboard.has_high_value_non_text_format() is True


def test_read_text_clipboard_success(monkeypatch):
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)
    monkeypatch.setattr(
        clipboard.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt == win32con.CF_UNICODETEXT,
    )
    monkeypatch.setattr(
        clipboard.win32clipboard, "GetClipboardData", lambda fmt: "hello world"
    )

    assert clipboard.read_text_clipboard() == "hello world"


def test_write_text_clipboard_success(monkeypatch):
    calls = []
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: calls.append("open"))
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: calls.append("close"))
    monkeypatch.setattr(clipboard.win32clipboard, "EmptyClipboard", lambda: calls.append("empty"))
    monkeypatch.setattr(
        clipboard.win32clipboard,
        "SetClipboardData",
        lambda fmt, data: calls.append((fmt, data)),
    )

    res = clipboard.write_text_clipboard("test string")
    assert res is True
    assert calls == ["open", "empty", (win32con.CF_UNICODETEXT, "test string"), "close"]


def test_write_text_clipboard_failure_returns_false(monkeypatch):
    def mock_open():
        raise RuntimeError("clipboard locked")

    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", mock_open)
    monkeypatch.setattr(clipboard.time, "sleep", lambda _: None)

    res = clipboard.write_text_clipboard("test string")
    assert res is False


def test_snapshot_clipboard_empty(monkeypatch):
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)
    # Enum returns 0 immediately (empty)
    monkeypatch.setattr(clipboard.win32clipboard, "EnumClipboardFormats", lambda _: 0)
    monkeypatch.setattr(ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 42)

    snap = clipboard.snapshot_clipboard()
    assert snap.status == SnapshotStatus.EMPTY
    assert snap.sequence == 42
    assert snap.text is None


def test_snapshot_clipboard_unsupported(monkeypatch):
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)

    # Enum returns CF_BITMAP then 0
    fmt_iter = iter([win32con.CF_BITMAP, 0])
    monkeypatch.setattr(clipboard.win32clipboard, "EnumClipboardFormats", lambda _: next(fmt_iter))
    monkeypatch.setattr(ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 42)

    snap = clipboard.snapshot_clipboard()
    assert snap.status == SnapshotStatus.UNSUPPORTED


def test_snapshot_clipboard_unsupported_even_with_text(monkeypatch):
    # If both text and CF_BITMAP are present, it must fail closed and return UNSUPPORTED
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)

    fmt_iter = iter([win32con.CF_UNICODETEXT, win32con.CF_BITMAP, 0])
    monkeypatch.setattr(clipboard.win32clipboard, "EnumClipboardFormats", lambda _: next(fmt_iter))
    monkeypatch.setattr(ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 42)

    snap = clipboard.snapshot_clipboard()
    assert snap.status == SnapshotStatus.UNSUPPORTED


def test_snapshot_clipboard_text(monkeypatch):
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)

    fmt_iter = iter([win32con.CF_UNICODETEXT, 0])
    monkeypatch.setattr(clipboard.win32clipboard, "EnumClipboardFormats", lambda _: next(fmt_iter))
    monkeypatch.setattr(
        clipboard.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt == win32con.CF_UNICODETEXT,
    )
    monkeypatch.setattr(
        clipboard.win32clipboard, "GetClipboardData", lambda fmt: "saved text"
    )
    monkeypatch.setattr(ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 100)

    snap = clipboard.snapshot_clipboard()
    assert snap.status == SnapshotStatus.TEXT
    assert snap.text == "saved text"
    assert snap.sequence == 100


def test_snapshot_clipboard_unavailable(monkeypatch):
    def mock_open():
        raise RuntimeError("locked")
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", mock_open)
    monkeypatch.setattr(clipboard.time, "sleep", lambda _: None)

    snap = clipboard.snapshot_clipboard()
    assert snap.status == SnapshotStatus.UNAVAILABLE


def test_restore_clipboard_if_owned_no_ownership():
    snap = clipboard.ClipboardSnapshot(status=SnapshotStatus.TEXT, text="hello")
    res = clipboard.restore_clipboard_if_owned(snap, None)
    assert res == RestoreOutcome.NO_OWNERSHIP


def test_restore_clipboard_if_owned_external_change(monkeypatch):
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: None)
    # Current sequence (999) does not match owned_seq (42)
    monkeypatch.setattr(ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 999)

    snap = clipboard.ClipboardSnapshot(status=SnapshotStatus.TEXT, text="hello")
    res = clipboard.restore_clipboard_if_owned(snap, 42)
    assert res == RestoreOutcome.EXTERNAL_CHANGE


def test_restore_clipboard_if_owned_success(monkeypatch):
    calls = []
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: calls.append("open"))
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: calls.append("close"))
    monkeypatch.setattr(clipboard.win32clipboard, "EmptyClipboard", lambda: calls.append("empty"))
    monkeypatch.setattr(
        clipboard.win32clipboard,
        "SetClipboardData",
        lambda fmt, data: calls.append((fmt, data)),
    )
    # Sequence matches
    monkeypatch.setattr(ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 42)

    snap = clipboard.ClipboardSnapshot(status=SnapshotStatus.TEXT, text="hello")
    res = clipboard.restore_clipboard_if_owned(snap, 42)
    assert res == RestoreOutcome.RESTORED
    assert calls == ["open", "empty", (win32con.CF_UNICODETEXT, "hello"), "close"]


def test_restore_clipboard_if_owned_failed_cleared(monkeypatch):
    calls = []
    monkeypatch.setattr(clipboard.win32clipboard, "OpenClipboard", lambda: calls.append("open"))
    monkeypatch.setattr(clipboard.win32clipboard, "CloseClipboard", lambda: calls.append("close"))
    monkeypatch.setattr(clipboard.win32clipboard, "EmptyClipboard", lambda: calls.append("empty"))

    # SetClipboardData fails
    def mock_set_data(fmt, data):
        raise RuntimeError("failed to set data")
    monkeypatch.setattr(clipboard.win32clipboard, "SetClipboardData", mock_set_data)
    monkeypatch.setattr(ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 42)

    snap = clipboard.ClipboardSnapshot(status=SnapshotStatus.TEXT, text="hello")
    res = clipboard.restore_clipboard_if_owned(snap, 42)

    assert res == RestoreOutcome.FAILED_CLEARED
    assert "empty" in calls


# --- privacy: foreign exception text must not reach the logs --------------------

_SENTINEL = "PRIVATE_CONTROL_TEXT"


def _raiser(*args, **kwargs):
    raise RuntimeError(_SENTINEL)


def test_write_clipboard_failure_exception_text_not_logged(monkeypatch, caplog):
    import logging
    from contextual_intelligence import clipboard as clip

    monkeypatch.setattr(clip.win32clipboard, "OpenClipboard", _raiser)
    monkeypatch.setattr(clip.time, "sleep", lambda s: None)

    with caplog.at_level(logging.DEBUG):
        assert clip.write_text_clipboard("x") is False

    assert _SENTINEL not in caplog.text
    assert "RuntimeError" in caplog.text  # class-only category survives


def test_snapshot_failure_exception_text_not_logged(monkeypatch, caplog):
    import logging
    from contextual_intelligence import clipboard as clip
    from contextual_intelligence.models import SnapshotStatus

    monkeypatch.setattr(clip.win32clipboard, "OpenClipboard", _raiser)
    monkeypatch.setattr(clip.time, "sleep", lambda s: None)

    with caplog.at_level(logging.DEBUG):
        snap = clip.snapshot_clipboard()

    assert snap.status == SnapshotStatus.UNAVAILABLE
    assert _SENTINEL not in caplog.text


def test_restore_commit_point_exception_text_not_logged(monkeypatch, caplog):
    import ctypes
    import logging
    from contextual_intelligence import clipboard as clip
    from contextual_intelligence.models import RestoreOutcome, SnapshotStatus

    monkeypatch.setattr(clip.win32clipboard, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clip.win32clipboard, "CloseClipboard", lambda: None)
    monkeypatch.setattr(clip.win32clipboard, "EmptyClipboard", lambda: None)
    monkeypatch.setattr(clip.win32clipboard, "SetClipboardData", _raiser)
    monkeypatch.setattr(
        ctypes.windll.user32, "GetClipboardSequenceNumber", lambda: 100
    )

    snap = clip.ClipboardSnapshot(status=SnapshotStatus.TEXT, text="orig", sequence=99)
    with caplog.at_level(logging.DEBUG):
        outcome = clip.restore_clipboard_if_owned(snap, owned_seq=100)

    assert outcome == RestoreOutcome.FAILED_CLEARED
    assert _SENTINEL not in caplog.text
