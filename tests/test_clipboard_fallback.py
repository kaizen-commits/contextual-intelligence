import pytest
import uiautomation as auto
from unittest.mock import MagicMock

from contextual_intelligence.capture import clipboard_fallback
from contextual_intelligence.capture.clipboard_fallback import ClipboardFallbackProvider
from contextual_intelligence.clipboard import ClipboardSnapshot
from contextual_intelligence.models import (
    CaptureError,
    CaptureTier,
    SnapshotStatus,
    RestoreOutcome,
    RestoreFailureFlavor,
    CaptureIntegrityError,
    ProtectedFieldError,
)


class MockFocusedControl:
    def __init__(self, is_password=False, pid=123, hwnd=456, top_name="notepad"):
        self.IsPassword = is_password
        self.ProcessId = pid
        self.NativeWindowHandle = hwnd
        self.ClassName = "Edit"
        self._top = MagicMock()
        self._top.NativeWindowHandle = hwnd
        self._top.Name = top_name
    def GetTopLevelControl(self):
        return self._top
    def GetPattern(self, pat_id):
        return None


class MockInitializer:
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture(autouse=True)
def setup_fallback_mocks(monkeypatch):
    monkeypatch.setattr(auto, "UIAutomationInitializerInThread", MockInitializer)
    monkeypatch.setattr("contextual_intelligence.capture.uia._process_image_name", lambda pid: "notepad.exe")
    monkeypatch.setattr(clipboard_fallback.user32, "SendInput", lambda n, ptr, sz: n)
    monkeypatch.setattr(clipboard_fallback, "_clear_modifiers", lambda: None)


def test_clipboard_capture_success(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    seq_counter = [100]
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: seq_counter[-1])

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)

    # Mock copy action to increment sequence number
    def mock_send_ctrl_c():
        seq_counter.append(101)
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", mock_send_ctrl_c)

    # Attribution mocks
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardOwner", lambda: 789)
    def mock_get_thread_process_id(hwnd, pid_ptr):
        pid_ptr.contents.value = 123
        return 1
    monkeypatch.setattr(clipboard_fallback.user32, "GetWindowThreadProcessId", mock_get_thread_process_id)

    # Owned read mock
    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", lambda: "newly copied selection")

    restore_calls = []
    def mock_restore(snap, owned_seq):
        restore_calls.append((snap, owned_seq))
        return RestoreOutcome.RESTORED
    monkeypatch.setattr(clipboard_fallback, "restore_clipboard_if_owned", mock_restore)

    payload = provider.capture()
    assert payload.selected_text == "newly copied selection"
    assert payload.tier == CaptureTier.CLIPBOARD
    assert payload.app_name == "notepad.exe"
    assert len(restore_calls) == 1
    assert restore_calls[0] == (snapshot_val, 101)


def test_clipboard_capture_password_preflight_blocks(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=True, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    def _fail_if_called():
        raise AssertionError("_send_ctrl_c must not run for protected fields")

    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", _fail_if_called)

    with pytest.raises(ProtectedFieldError, match="password field; fallback aborted"):
        provider.capture()


def test_clipboard_capture_preflight_none_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    monkeypatch.setattr(auto, "GetFocusedControl", lambda: None)

    with pytest.raises(CaptureError, match="no focused control during preflight"):
        provider.capture()


def test_clipboard_capture_preflight_exception_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    def mock_get_focused():
        raise RuntimeError("UIA failed")
    monkeypatch.setattr(auto, "GetFocusedControl", mock_get_focused)

    with pytest.raises(CaptureError, match="focused control resolution failed during preflight"):
        provider.capture()


def test_clipboard_capture_snapshot_unavailable_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.UNAVAILABLE)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)

    with pytest.raises(CaptureError, match="clipboard is unavailable/locked"):
        provider.capture()


def test_clipboard_capture_snapshot_unsupported_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.UNSUPPORTED)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)

    with pytest.raises(CaptureError, match="clipboard contains unsupported formats"):
        provider.capture()


def test_clipboard_capture_pre_send_drift_re_snapshots_once(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    # First snapshot returns sequence 100
    snap_calls = []
    def mock_snap():
        seq = 100 if len(snap_calls) == 0 else 101
        snap = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="orig", sequence=seq)
        snap_calls.append(snap)
        return snap
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", mock_snap)

    # GetClipboardSequenceNumber returns 101 pre-send (drifted!)
    seq_counter = [101]
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: seq_counter[-1])

    # Mock copy, attribution, read, restore to make capture succeed
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: seq_counter.append(102))
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardOwner", lambda: 789)
    def mock_get_thread_process_id(hwnd, pid_ptr):
        pid_ptr.contents.value = 123
        return 1
    monkeypatch.setattr(clipboard_fallback.user32, "GetWindowThreadProcessId", mock_get_thread_process_id)
    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", lambda: "copied text")
    monkeypatch.setattr(clipboard_fallback, "restore_clipboard_if_owned", lambda s, o: RestoreOutcome.RESTORED)

    payload = provider.capture()
    assert payload.selected_text == "copied text"
    # snapshot_clipboard should have been called twice (once initial, once after drift)
    assert len(snap_calls) == 2


def test_clipboard_capture_pre_send_double_drift_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snap_calls = []
    def mock_snap():
        seq = 100 if len(snap_calls) == 0 else 101
        snap = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="orig", sequence=seq)
        snap_calls.append(snap)
        return snap
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", mock_snap)

    # GetClipboardSequenceNumber returns 101 on first check (drift), then 102 on second check (drift again!)
    seq_counter = iter([101, 102])
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: next(seq_counter))

    with pytest.raises(CaptureError, match="clipboard sequence drifted repeatedly; aborting"):
        provider.capture()


def test_clipboard_capture_target_revalidation_fails_on_password_change(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    # Preflight returns non-password control
    # Revalidation returns password control!
    controls = iter([
        MockFocusedControl(is_password=False, pid=123, hwnd=456),
        MockFocusedControl(is_password=True, pid=123, hwnd=456),
    ])
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: next(controls))

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: 100)

    with pytest.raises(ProtectedFieldError, match="focused control became a password field"):
        provider.capture()


def test_clipboard_capture_partial_send_input_raises_and_compensates(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: 100)

    # SendInput returns partial count (e.g. 2 instead of 4)
    send_input_calls = []
    def mock_send_input(n, ptr, sz):
        send_input_calls.append(n)
        if len(send_input_calls) == 1:
            return 2 # partial
        return n # compensation succeeds
    monkeypatch.setattr(clipboard_fallback.user32, "SendInput", mock_send_input)

    with pytest.raises(CaptureError, match="SendInput failed"):
        provider.capture()

    # SendInput should be called twice: first for copy (4), second for compensation (2)
    assert send_input_calls == [4, 2]


def test_clipboard_capture_unattributed_owner_null_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)

    seq_counter = [100]
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: seq_counter[-1])
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: seq_counter.append(101))

    # Owner window is NULL (0)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardOwner", lambda: 0)
    monkeypatch.setattr(clipboard_fallback, "restore_clipboard_if_owned", lambda s, o: RestoreOutcome.RESTORED)

    with pytest.raises(CaptureError, match="owner window was NULL"):
        provider.capture()


def test_clipboard_capture_unattributed_owner_image_mismatch_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)

    seq_counter = [100]
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: seq_counter[-1])
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: seq_counter.append(101))

    # Owner window has different PID (456) -> resolves to "explorer.exe" (not "notepad.exe")
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardOwner", lambda: 789)
    def mock_get_thread_process_id(hwnd, pid_ptr):
        pid_ptr.contents.value = 456
        return 1
    monkeypatch.setattr(clipboard_fallback.user32, "GetWindowThreadProcessId", mock_get_thread_process_id)
    monkeypatch.setattr("contextual_intelligence.capture.uia._process_image_name", lambda pid: "explorer.exe" if pid == 456 else "notepad.exe")

    monkeypatch.setattr(clipboard_fallback, "restore_clipboard_if_owned", lambda s, o: RestoreOutcome.RESTORED)

    with pytest.raises(CaptureError, match="owner image.*does not match target"):
        provider.capture()


def test_clipboard_capture_stable_read_violation_aborts(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)

    # Call 1 (drift check): 100
    # Call 2 (poll -> detected_seq/owned_seq): 101
    # Call 3 (stable read check): 102 — a second writer landed after detection
    seq_vals = [100, 101, 102]
    seq_iter = iter(seq_vals)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: next(seq_iter))
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: None)

    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardOwner", lambda: 789)
    def mock_get_thread_process_id(hwnd, pid_ptr):
        pid_ptr.contents.value = 123
        return 1
    monkeypatch.setattr(clipboard_fallback.user32, "GetWindowThreadProcessId", mock_get_thread_process_id)
    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", lambda: "copied text")
    restore_calls = []

    def mock_restore(snap, owned_seq):
        restore_calls.append(owned_seq)
        return RestoreOutcome.RESTORED

    monkeypatch.setattr(clipboard_fallback, "restore_clipboard_if_owned", mock_restore)

    with pytest.raises(CaptureError, match="sequence changed during read; interference detected"):
        provider.capture()

    # Ownership is the DETECTION-time sequence (101), never a post-attribution
    # re-read (which would have been 102 — the second writer's value, letting
    # restore overwrite that writer's content).
    assert restore_calls == [101]


def test_clipboard_capture_restore_failed_raises_integrity_error_never_wrote(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: 100)

    # Mock success up to restore
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: None)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardOwner", lambda: 789)
    def mock_get_thread_process_id(hwnd, pid_ptr):
        pid_ptr.contents.value = 123
        return 1
    monkeypatch.setattr(clipboard_fallback.user32, "GetWindowThreadProcessId", mock_get_thread_process_id)
    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", lambda: "copied text")

    # Restore fails with FAILED outcome (never wrote)
    monkeypatch.setattr(clipboard_fallback, "restore_clipboard_if_owned", lambda s, o: RestoreOutcome.FAILED)

    with pytest.raises(CaptureIntegrityError) as exc_info:
        provider.capture()
    assert exc_info.value.flavor == RestoreFailureFlavor.NEVER_WROTE


def test_clipboard_capture_restore_failed_cleared_raises_integrity_error_cleared(monkeypatch):
    provider = ClipboardFallbackProvider()
    provider.arm()

    mock_focused = MockFocusedControl(is_password=False, pid=123, hwnd=456)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: mock_focused)

    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="original clipboard", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: 100)

    # Mock success up to restore
    monkeypatch.setattr(clipboard_fallback, "_send_ctrl_c", lambda: None)
    monkeypatch.setattr(clipboard_fallback.user32, "GetClipboardOwner", lambda: 789)
    def mock_get_thread_process_id(hwnd, pid_ptr):
        pid_ptr.contents.value = 123
        return 1
    monkeypatch.setattr(clipboard_fallback.user32, "GetWindowThreadProcessId", mock_get_thread_process_id)
    monkeypatch.setattr(clipboard_fallback, "_save_clipboard", lambda: "copied text")

    # Restore fails with FAILED_CLEARED outcome (cleared)
    monkeypatch.setattr(clipboard_fallback, "restore_clipboard_if_owned", lambda s, o: RestoreOutcome.FAILED_CLEARED)

    with pytest.raises(CaptureIntegrityError) as exc_info:
        provider.capture()
    assert exc_info.value.flavor == RestoreFailureFlavor.CLEARED


# --- privacy: foreign exception text must not reach reasons or logs -------------

_SENTINEL = "PRIVATE_CONTROL_TEXT"


def test_preflight_resolution_exception_text_not_leaked(monkeypatch, caplog):
    import logging

    provider = ClipboardFallbackProvider()
    provider.arm()

    def boom():
        raise RuntimeError(_SENTINEL)

    monkeypatch.setattr(auto, "GetFocusedControl", boom)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(CaptureError) as excinfo:
            provider.capture()

    assert _SENTINEL not in str(excinfo.value)
    assert "RuntimeError" in str(excinfo.value)  # class-only category survives
    assert _SENTINEL not in caplog.text


def test_preflight_property_exception_text_not_leaked(monkeypatch, caplog):
    import logging

    class _PropertyBoom:
        IsPassword = False

        @property
        def ProcessId(self):
            raise RuntimeError(_SENTINEL)

        def GetTopLevelControl(self):
            return None

    provider = ClipboardFallbackProvider()
    provider.arm()
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: _PropertyBoom())

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(CaptureError) as excinfo:
            provider.capture()

    assert _SENTINEL not in str(excinfo.value)
    assert _SENTINEL not in caplog.text


def test_revalidation_exception_text_not_leaked(monkeypatch, caplog):
    import logging

    calls = {"n": 0}

    def focused_seq():
        calls["n"] += 1
        if calls["n"] == 1:
            return MockFocusedControl(is_password=False, pid=123, hwnd=456)
        raise RuntimeError(_SENTINEL)

    monkeypatch.setattr(auto, "GetFocusedControl", focused_seq)
    snapshot_val = ClipboardSnapshot(status=SnapshotStatus.TEXT, text="orig", sequence=100)
    monkeypatch.setattr(clipboard_fallback, "snapshot_clipboard", lambda: snapshot_val)
    monkeypatch.setattr(
        clipboard_fallback.user32, "GetClipboardSequenceNumber", lambda: 100
    )

    provider = ClipboardFallbackProvider()
    provider.arm()

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(CaptureError) as excinfo:
            provider.capture()

    assert _SENTINEL not in str(excinfo.value)
    assert _SENTINEL not in caplog.text
