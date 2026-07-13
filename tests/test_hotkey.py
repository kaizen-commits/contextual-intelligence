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

    def PeekMessageW(self, lpMsg, hwnd, min_val, max_val, remove):
        # Real PeekMessageW creates the thread's message queue as a side effect.
        return 0

    def GetMessageW(self, lpMsg, hwnd, min_val, max_val):
        if not self.messages:
            return 0
        entry = self.messages.pop(0)
        if entry == "ERROR":
            return -1
        msg_type, wparam = entry
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


def test_get_message_error_breaks_loop_without_dispatch(monkeypatch):
    """GetMessageW returns a tri-state: -1 is an error and the MSG must not be
    dispatched (`!= 0` would loop on garbage forever)."""
    mock = MockUser32(messages=["ERROR", (WM_HOTKEY, LOOKUP_HOTKEY_ID)])
    monkeypatch.setattr("ctypes.windll.user32", mock)

    called = []
    run_hotkey_loop(hotkey_map={LOOKUP_HOTKEY_ID: (0x44, lambda: called.append(True))})

    assert called == []  # the message after the error was never consumed
    assert mock.messages  # loop exited on the error, not by draining the queue
    assert mock.unregistered == [LOOKUP_HOTKEY_ID]


def test_ready_fires_after_queue_creation_and_stopping_preempts_loop(monkeypatch):
    """The stop handshake: on_ready fires once WM_QUIT can be posted safely, and
    a stop requested before the loop starts is honored without entering it."""
    import threading

    mock = MockUser32(messages=[(WM_HOTKEY, LOOKUP_HOTKEY_ID)])
    monkeypatch.setattr("ctypes.windll.user32", mock)

    events = []
    stopping = threading.Event()
    stopping.set()  # stop requested before the loop begins

    run_hotkey_loop(
        hotkey_map={LOOKUP_HOTKEY_ID: (0x44, lambda: events.append("hotkey"))},
        on_ready=lambda: events.append("ready"),
        stopping=stopping,
    )

    assert events == ["ready"]  # ready fired; the queued hotkey was never dispatched
    assert mock.unregistered == [LOOKUP_HOTKEY_ID]  # cleanup ran on the early-out path
