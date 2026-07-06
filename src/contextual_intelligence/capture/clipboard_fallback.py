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

import win32clipboard
import win32con

from contextual_intelligence.models import CaptureError, CaptureTier, ContextPayload

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002


class ListenerState(StrEnum):
    DISARMED = "disarmed"
    ARMED = "armed"
    CAPTURING = "capturing"


def _save_clipboard() -> str | None:
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                    data = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
                return None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.02)
    return None


def _restore_clipboard(text: str | None) -> None:
    if text is None:
        return
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception:
            time.sleep(0.02)


def _clear_modifiers() -> None:
    """Release any modifier keys held down from triggering the hotkey (e.g., Alt from Ctrl+Alt+D)."""
    for vk in (VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)


def _send_ctrl_c() -> None:
    _clear_modifiers()
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


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
        saved_text = _save_clipboard()
        seq_before = user32.GetClipboardSequenceNumber()

        try:
            _send_ctrl_c()

            # Poll up to 400ms for sequence number change (PowerToys lesson)
            start_wait = time.perf_counter()
            seq_changed = False
            while time.perf_counter() - start_wait < 0.4:
                if user32.GetClipboardSequenceNumber() != seq_before:
                    seq_changed = True
                    break
                time.sleep(0.01)

            if not seq_changed:
                raise CaptureError(
                    "clipboard sequence number did not increment (Ctrl+C failed or no selection)",
                    CaptureTier.CLIPBOARD,
                )

            copied_text = _save_clipboard() or ""
        finally:
            _restore_clipboard(saved_text)

        if not copied_text or not copied_text.strip():
            raise CaptureError(
                "clipboard copy produced no text or empty selection",
                CaptureTier.CLIPBOARD,
            )

        before = ""
        after = ""
        app_name = ""
        window_title = ""
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread(debug=False):
                focused = auto.GetFocusedControl()
                if focused:
                    from contextual_intelligence.capture.uia import _process_image_name
                    app_name = _process_image_name(focused.ProcessId)
                    top = focused.GetTopLevelControl()
                    window_title = (top.Name if top else "") or ""

                    pattern = focused.GetPattern(auto.PatternId.TextPattern)
                    if not pattern and hasattr(focused, "GetTextPattern"):
                        pattern = focused.GetTextPattern()
                    if pattern:
                        doc_range = pattern.DocumentRange
                        if doc_range:
                            full = doc_range.GetText(-1) or ""
                            idx = full.find(copied_text)
                            if idx >= 0:
                                before = full[:idx]
                                after = full[idx + len(copied_text):]
        except Exception as exc:
            log.debug("fallback document range context extraction failed: %s", exc)

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

