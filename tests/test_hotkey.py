import ctypes
import pytest

from contextual_intelligence.hotkey import (
    LOOKUP_HOTKEY_ID,
    PASTE_HOTKEY_ID,
    WM_HOTKEY,
    HotkeyError,
    run_hotkey_loop,
)


class MockUser32:
    def __init__(self, fail_ids=None, messages=None):
        self.fail_ids = set(fail_ids or [])
        self.messages = list(messages or [])
        self.registered = []
        self.unregistered = []

    def RegisterHotKey(self, hwnd, id_, modifiers, vk):
        if id_ in self.fail_ids:
            return 0
        self.registered.append((id_, vk))
        return 1

    def UnregisterHotKey(self, hwnd, id_):
        self.unregistered.append(id_)
        return 1

    def GetMessageW(self, lpMsg, hwnd, min_val, max_val):
        if not self.messages:
            return 0
        msg_type, wparam = self.messages.pop(0)
        msg_obj = ctypes.cast(lpMsg, ctypes.POINTER(ctypes.wintypes.MSG)).contents
        msg_obj.message = msg_type
        msg_obj.wParam = wparam
        return 1

    def TranslateMessage(self, lpMsg):
        return 1

    def DispatchMessageW(self, lpMsg):
        return 1


def test_legacy_single_hotkey(monkeypatch):
    mock = MockUser32(messages=[(WM_HOTKEY, LOOKUP_HOTKEY_ID)])
    monkeypatch.setattr("ctypes.windll.user32", mock)

    called = []
    run_hotkey_loop(callback=lambda: called.append("lookup"), vk=0x44)

    assert mock.registered == [(LOOKUP_HOTKEY_ID, 0x44)]
    assert mock.unregistered == [LOOKUP_HOTKEY_ID]
    assert called == ["lookup"]


def test_multi_hotkey_map(monkeypatch):
    mock = MockUser32(messages=[(WM_HOTKEY, PASTE_HOTKEY_ID), (WM_HOTKEY, LOOKUP_HOTKEY_ID)])
    monkeypatch.setattr("ctypes.windll.user32", mock)

    events = []
    hm = {
        LOOKUP_HOTKEY_ID: (0x44, lambda: events.append("lookup")),
        PASTE_HOTKEY_ID: (0x56, lambda: events.append("paste")),
    }
    run_hotkey_loop(hotkey_map=hm)

    assert set(mock.registered) == {(LOOKUP_HOTKEY_ID, 0x44), (PASTE_HOTKEY_ID, 0x56)}
    assert set(mock.unregistered) == {LOOKUP_HOTKEY_ID, PASTE_HOTKEY_ID}
    assert events == ["paste", "lookup"]


def test_degradation_semantics(monkeypatch):
    """If one hotkey fails to register, loop still runs for the succeeding one."""
    mock = MockUser32(fail_ids=[LOOKUP_HOTKEY_ID], messages=[(WM_HOTKEY, PASTE_HOTKEY_ID)])
    monkeypatch.setattr("ctypes.windll.user32", mock)

    events = []
    failures = []
    hm = {
        LOOKUP_HOTKEY_ID: (0x44, lambda: events.append("lookup")),
        PASTE_HOTKEY_ID: (0x56, lambda: events.append("paste")),
    }
    run_hotkey_loop(
        hotkey_map=hm,
        on_registration_failure=lambda hid, vk: failures.append((hid, vk)),
    )

    assert mock.registered == [(PASTE_HOTKEY_ID, 0x56)]
    assert mock.unregistered == [PASTE_HOTKEY_ID]
    assert failures == [(LOOKUP_HOTKEY_ID, 0x44)]
    assert events == ["paste"]


def test_all_hotkeys_fail_raises_error(monkeypatch):
    mock = MockUser32(fail_ids=[LOOKUP_HOTKEY_ID, PASTE_HOTKEY_ID])
    monkeypatch.setattr("ctypes.windll.user32", mock)

    hm = {
        LOOKUP_HOTKEY_ID: (0x44, lambda: None),
        PASTE_HOTKEY_ID: (0x56, lambda: None),
    }
    with pytest.raises(HotkeyError, match="All hotkeys failed to register"):
        run_hotkey_loop(hotkey_map=hm)
