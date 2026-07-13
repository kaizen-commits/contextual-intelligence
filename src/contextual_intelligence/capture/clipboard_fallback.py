"""Fallback capture tier: deterministic clipboard automation.

Implements the lifecycle contract:
- one active capture at a time
- explicit arm/disarm transitions; capture is only legal while armed
- capture always disarms on exit, success or failure
- disarm is idempotent and legal from any state (cancellation path)

And the deterministic clipboard automation:
- save clipboard -> SendInput Ctrl+C -> verify via clipboard sequence-number polling
  (WM_COPY can report success without updating the clipboard in Electron/Chromium)
- read selection -> restore clipboard with retry.
- attempt UIA TextPattern.DocumentRange on the focused element for whole-document text.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from enum import StrEnum
from ctypes import wintypes

from contextual_intelligence.clipboard import (
    snapshot_clipboard,
    restore_clipboard_if_owned,
    read_text_clipboard as _save_clipboard,
)
from contextual_intelligence.models import (
    SnapshotStatus,
    RestoreOutcome,
    RestoreFailureFlavor,
    CaptureError,
    CaptureIntegrityError,
    ProtectedFieldError,
    CaptureTier,
    ContextPayload,
)

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
user32.GetClipboardOwner.restype = wintypes.HWND
user32.GetClipboardOwner.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002


# Ctypes structures for SendInput
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


INPUT_KEYBOARD = 1

# Bound the fallback's whole-document context read. GetText(-1) on a large
# document is an unbounded cross-process UIA call that can take seconds and
# make the target app janky (especially Chromium/Electron hosts).
_MAX_DOC_READ_CHARS = 20_000
_MAX_CONTEXT_CHARS_PER_SIDE = 4_000


def _send_inputs(inputs: list[INPUT]) -> None:
    n_inputs = len(inputs)
    input_array = (INPUT * n_inputs)(*inputs)
    sent = user32.SendInput(n_inputs, ctypes.byref(input_array), ctypes.sizeof(INPUT))
    if sent != n_inputs:
        # Send best-effort compensating key-ups for VK_CONTROL and VK_C to avoid stuck keys
        compensating = []
        for vk in (VK_C, VK_CONTROL):
            compensating.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    union=INPUT_UNION(
                        ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                    ),
                )
            )
        comp_array = (INPUT * len(compensating))(*compensating)
        user32.SendInput(len(compensating), ctypes.byref(comp_array), ctypes.sizeof(INPUT))

        raise CaptureError(
            f"SendInput failed: sent {sent} of {n_inputs} inputs",
            CaptureTier.CLIPBOARD,
        )


class ListenerState(StrEnum):
    DISARMED = "disarmed"
    ARMED = "armed"
    CAPTURING = "capturing"


def _clear_modifiers() -> None:
    """Release any modifier keys held down from triggering the hotkey (e.g., Alt from Ctrl+Alt+D)."""
    inputs = []
    for vk in (VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            inputs.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    union=INPUT_UNION(
                        ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                    ),
                )
            )
    if inputs:
        _send_inputs(inputs)
        # Wait for modifier release to register fully (avoid alt-key combinations clashing)
        time.sleep(0.05)


def _send_ctrl_c() -> None:
    _clear_modifiers()
    inputs = [
        # Ctrl Down
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
            ),
        ),
        # C Down
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(wVk=VK_C, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
            ),
        ),
        # C Up
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(wVk=VK_C, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            ),
        ),
        # Ctrl Up
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            ),
        ),
    ]
    _send_inputs(inputs)


class ClipboardFallbackProvider:
    name = "clipboard"

    def __init__(self):
        self._lock = threading.Lock()
        self._state = ListenerState.DISARMED

    @property
    def state(self) -> ListenerState:
        return self._state

    def arm(self) -> None:
        with self._lock:
            if self._state is not ListenerState.DISARMED:
                raise CaptureError(
                    f"cannot arm from state {self._state}", CaptureTier.CLIPBOARD
                )
            self._state = ListenerState.ARMED
            log.debug("clipboard fallback armed")

    def disarm(self) -> None:
        """Legal from any state — this is the cancellation/teardown path."""
        with self._lock:
            if self._state is not ListenerState.DISARMED:
                log.debug("clipboard fallback disarmed (was %s)", self._state)
            self._state = ListenerState.DISARMED

    def capture(self) -> ContextPayload:
        with self._lock:
            if self._state is not ListenerState.ARMED:
                raise CaptureError(
                    f"capture requires armed state, was {self._state}",
                    CaptureTier.CLIPBOARD,
                )
            self._state = ListenerState.CAPTURING
        try:
            return self._do_capture()
        finally:
            self.disarm()

    def _do_capture(self) -> ContextPayload:
        # 1. Protected-field preflight
        import uiautomation as auto
        from contextual_intelligence.capture.uia import _process_image_name

        with auto.UIAutomationInitializerInThread(debug=False):
            try:
                focused = auto.GetFocusedControl()
            except Exception as exc:
                # Class name only: UIA exception text can carry focused-control
                # metadata, and CaptureError.reason reaches diagnostic logs.
                raise CaptureError(
                    f"focused control resolution failed during preflight ({type(exc).__name__})",
                    CaptureTier.CLIPBOARD,
                )

            if focused is None:
                raise CaptureError("no focused control during preflight", CaptureTier.CLIPBOARD)

            try:
                if getattr(focused, "IsPassword", False):
                    raise ProtectedFieldError(
                        "focused control is a password field; fallback aborted",
                        CaptureTier.CLIPBOARD,
                    )
                target_pid = focused.ProcessId
                target_image = _process_image_name(target_pid)
                top = focused.GetTopLevelControl()
                target_hwnd = top.NativeWindowHandle if top else 0
            except Exception as exc:
                if isinstance(exc, ProtectedFieldError):
                    raise
                raise CaptureError(
                    f"failed to read UIA properties during preflight ({type(exc).__name__})",
                    CaptureTier.CLIPBOARD,
                )

        # 2. Snapshot
        snapshot = snapshot_clipboard()
        if snapshot.status == SnapshotStatus.UNAVAILABLE:
            raise CaptureError("clipboard is unavailable/locked", CaptureTier.CLIPBOARD)
        if snapshot.status == SnapshotStatus.UNSUPPORTED:
            raise CaptureError("clipboard contains unsupported formats", CaptureTier.CLIPBOARD)

        # 3. Pre-send drift check + revalidation
        seq_before = user32.GetClipboardSequenceNumber()
        if seq_before != snapshot.sequence:
            log.info("clipboard sequence drifted pre-send; taking new snapshot")
            snapshot = snapshot_clipboard()
            if snapshot.status == SnapshotStatus.UNAVAILABLE:
                raise CaptureError("clipboard is unavailable/locked after drift", CaptureTier.CLIPBOARD)
            if snapshot.status == SnapshotStatus.UNSUPPORTED:
                raise CaptureError("clipboard contains unsupported formats after drift", CaptureTier.CLIPBOARD)
            seq_before = user32.GetClipboardSequenceNumber()
            if seq_before != snapshot.sequence:
                raise CaptureError("clipboard sequence drifted repeatedly; aborting", CaptureTier.CLIPBOARD)

        with auto.UIAutomationInitializerInThread(debug=False):
            try:
                refocused = auto.GetFocusedControl()
                if refocused is None:
                    raise CaptureError("no focused control during revalidation", CaptureTier.CLIPBOARD)
                if refocused.ProcessId != target_pid:
                    raise CaptureError("focused process changed during preflight", CaptureTier.CLIPBOARD)
                retop = refocused.GetTopLevelControl()
                re_hwnd = retop.NativeWindowHandle if retop else 0
                if re_hwnd != target_hwnd:
                    raise CaptureError("focused top-level window changed during preflight", CaptureTier.CLIPBOARD)
                if getattr(refocused, "IsPassword", False):
                    raise ProtectedFieldError(
                        "focused control became a password field; fallback aborted",
                        CaptureTier.CLIPBOARD,
                    )
            except Exception as exc:
                if isinstance(exc, (ProtectedFieldError, CaptureError)):
                    raise
                raise CaptureError(
                    f"target revalidation failed ({type(exc).__name__})",
                    CaptureTier.CLIPBOARD,
                )

        owned_seq = None
        copied_text = ""
        try:
            # 4. Copy
            _send_ctrl_c()

            # Poll up to 400ms for sequence number change, recording the exact
            # sequence value we detected — that value (not a later re-read) is
            # the only write attribution can vouch for.
            start_wait = time.perf_counter()
            detected_seq = None
            while time.perf_counter() - start_wait < 0.4:
                current_seq = user32.GetClipboardSequenceNumber()
                if current_seq != seq_before:
                    detected_seq = current_seq
                    break
                time.sleep(0.01)

            if detected_seq is None:
                raise CaptureError(
                    "clipboard sequence number did not increment (Ctrl+C failed or no selection)",
                    CaptureTier.CLIPBOARD,
                )

            # 5. Attribution
            owner_hwnd = user32.GetClipboardOwner()
            if not owner_hwnd:
                raise CaptureError(
                    "clipboard changed but owner window was NULL; unattributed",
                    CaptureTier.CLIPBOARD,
                )

            owner_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(owner_hwnd, ctypes.pointer(owner_pid))
            if not owner_pid.value:
                raise CaptureError(
                    "clipboard changed but owner PID was invalid; unattributed",
                    CaptureTier.CLIPBOARD,
                )

            owner_image = _process_image_name(owner_pid.value)
            if not owner_image or owner_image.lower() != target_image.lower():
                raise CaptureError(
                    f"clipboard changed but owner image ({owner_image}) does not match target ({target_image}); unattributed",
                    CaptureTier.CLIPBOARD,
                )

            # Adopt the detection-time sequence, NOT a fresh read: a fresh read
            # here would claim ownership of any write that landed after the
            # owner check, and the restore stage would then overwrite it. A
            # write landing between detection and here either flips the owner
            # (attribution abort above) or moves the sequence past detected_seq
            # (stable-read abort below / EXTERNAL_CHANGE no-op in restore).
            owned_seq = detected_seq

            # 6. Owned read
            copied_text = _save_clipboard()
            if copied_text is None:
                raise CaptureError(
                    "failed to read copied text from clipboard",
                    CaptureTier.CLIPBOARD,
                )

            # Stable read check
            seq_after_read = user32.GetClipboardSequenceNumber()
            if seq_after_read != owned_seq:
                raise CaptureError(
                    "clipboard sequence changed during read; interference detected",
                    CaptureTier.CLIPBOARD,
                )

        finally:
            # 7. Restore
            outcome = restore_clipboard_if_owned(snapshot, owned_seq)
            if outcome == RestoreOutcome.EXTERNAL_CHANGE:
                log.warning("clipboard changed externally during fallback capture; leaving it untouched")
            elif outcome == RestoreOutcome.FAILED:
                raise CaptureIntegrityError(
                    "clipboard restore failed; captured selection remains on clipboard",
                    RestoreFailureFlavor.NEVER_WROTE,
                    CaptureTier.CLIPBOARD,
                )
            elif outcome == RestoreOutcome.FAILED_CLEARED:
                raise CaptureIntegrityError(
                    "clipboard restore failed; original content lost, clipboard cleared",
                    RestoreFailureFlavor.CLEARED,
                    CaptureTier.CLIPBOARD,
                )

        if not copied_text or not copied_text.strip():
            raise CaptureError(
                "clipboard copy produced no text or empty selection",
                CaptureTier.CLIPBOARD,
            )

        before = ""
        after = ""
        app_name = target_image
        window_title = ""
        try:
            with auto.UIAutomationInitializerInThread(debug=False):
                # Retrieve control again in UIA context to extract document context
                focused = auto.GetFocusedControl()
                if focused and focused.ProcessId == target_pid:
                    top = focused.GetTopLevelControl()
                    window_title = (top.Name if top else "") or ""

                    pattern = focused.GetPattern(auto.PatternId.TextPattern)
                    if not pattern and hasattr(focused, "GetTextPattern"):
                        pattern = focused.GetTextPattern()
                    if pattern:
                        doc_range = pattern.DocumentRange
                        if doc_range:
                            t_read = time.perf_counter()
                            full = doc_range.GetText(_MAX_DOC_READ_CHARS) or ""
                            log.debug(
                                "fallback doc-range read %d chars in %.2fs",
                                len(full), time.perf_counter() - t_read,
                            )
                            idx = full.find(copied_text)
                            if idx >= 0:
                                before = full[:idx][-_MAX_CONTEXT_CHARS_PER_SIDE:]
                                after = full[idx + len(copied_text):][:_MAX_CONTEXT_CHARS_PER_SIDE]
        except Exception as exc:
            log.debug(
                "fallback document range context extraction failed (%s)", type(exc).__name__
            )

        return ContextPayload(
            selected_text=copied_text,
            before=before,
            after=after,
            app_name=app_name,
            window_title=window_title,
            tier=CaptureTier.CLIPBOARD,
        )


class ArmedClipboardCapture:
    """Orchestrator-facing adapter: performs the full arm -> capture -> disarm
    cycle per attempt, so the provider itself never relaxes the armed-state
    requirement."""

    name = "clipboard"

    def __init__(self, provider: ClipboardFallbackProvider | None = None):
        self.provider = provider or ClipboardFallbackProvider()

    def capture(self) -> ContextPayload:
        self.provider.arm()
        try:
            return self.provider.capture()
        finally:
            self.provider.disarm()
