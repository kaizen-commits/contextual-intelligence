import win32con

from contextual_intelligence import clipboard


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
