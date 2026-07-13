"""Primary capture tier: Microsoft UI Automation TextPattern.

Reads the current selection from the focused element and expands the range
by N characters each side for surrounding context. Works in Chromium
browsers, Word, Notepad, and most WPF/WinForms apps; the app matrix in
Phase 1 records where it doesn't.
"""

from __future__ import annotations

import ctypes
import logging

from contextual_intelligence.models import CaptureError, CaptureTier, ContextPayload

log = logging.getLogger(__name__)

# How many ancestors to walk when the focused element itself lacks TextPattern
# (e.g. focus sits on a child of the document control).
_ANCESTOR_SEARCH_DEPTH = 5


def get_process_image_name(pid: int) -> str:
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = ctypes.c_ulong(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1]
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:
        log.debug(
            "process image name lookup failed for pid %s (%s)",
            pid,
            type(exc).__name__,
        )
        return ""


_process_image_name = get_process_image_name


def get_foreground_app_name() -> str:
    """Get the process image name of the current foreground window non-blockingly."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            return get_process_image_name(pid.value)
    except Exception as exc:
        log.debug("foreground app name capture failed (%s)", type(exc).__name__)
    return ""


class UiaCaptureProvider:
    name = "uia"

    def __init__(self, context_chars_per_side: int = 1500):
        self._context_chars = context_chars_per_side

    def capture(self) -> ContextPayload:
        # Imported here so the module stays importable (and testable) off-thread;
        # uiautomation initializes COM at import/use time.
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            focused = auto.GetFocusedControl()
            if focused is None:
                raise CaptureError("no focused control", CaptureTier.UIA)

            if getattr(focused, "IsPassword", False):
                raise CaptureError("focused control is a password field", CaptureTier.UIA)

            pattern = self._find_text_pattern(focused, auto)
            if pattern is None:
                raise CaptureError(
                    f"no TextPattern on focused control or ancestors "
                    f"(class={focused.ClassName!r})",
                    CaptureTier.UIA,
                )

            selection = pattern.GetSelection()
            if not selection:
                raise CaptureError("no selection ranges", CaptureTier.UIA)

            sel_range = selection[0]
            selected = sel_range.GetText(-1) or ""
            if not selected.strip():
                raise CaptureError("selection is empty", CaptureTier.UIA)

            before, after = self._surrounding(sel_range, selected, auto)

            top = focused.GetTopLevelControl()
            payload = ContextPayload(
                selected_text=selected,
                before=before,
                after=after,
                app_name=get_process_image_name(focused.ProcessId),
                window_title=(top.Name if top else "") or "",
                tier=CaptureTier.UIA,
            )
            return payload

    def _find_text_pattern(self, control, auto):
        current = control
        for _ in range(_ANCESTOR_SEARCH_DEPTH):
            if current is None:
                return None
            try:
                pattern = current.GetPattern(auto.PatternId.TextPattern)
            except Exception:
                pattern = None
            if pattern is not None:
                return pattern
            current = current.GetParentControl()
        return None

    def _surrounding(self, sel_range, selected: str, auto) -> tuple[str, str]:
        """Expand surrounding context by cloning the selection range and moving
        endpoints independently. This avoids repeated-term misalignment when
        the selected term appears multiple times in the context window."""
        before = ""
        after = ""
        try:
            before_range = sel_range.Clone()
            before_range.MoveEndpointByUnit(
                auto.TextPatternRangeEndpoint.Start, auto.TextUnit.Character,
                -self._context_chars,
            )
            before_range.MoveEndpointByRange(
                auto.TextPatternRangeEndpoint.End, sel_range,
                auto.TextPatternRangeEndpoint.Start,
            )
            before = before_range.GetText(-1) or ""
        except Exception as exc:
            log.debug("before-context expansion failed (%s)", type(exc).__name__)

        try:
            after_range = sel_range.Clone()
            after_range.MoveEndpointByUnit(
                auto.TextPatternRangeEndpoint.End, auto.TextUnit.Character,
                self._context_chars,
            )
            after_range.MoveEndpointByRange(
                auto.TextPatternRangeEndpoint.Start, sel_range,
                auto.TextPatternRangeEndpoint.End,
            )
            after = after_range.GetText(-1) or ""
        except Exception as exc:
            log.debug("after-context expansion failed (%s)", type(exc).__name__)

        # Fallback to string splitting if endpoint manipulation returned empty
        # when an expanded range would have worked.
        if not before and not after:
            try:
                expanded = sel_range.Clone()
                expanded.MoveEndpointByUnit(
                    auto.TextPatternRangeEndpoint.Start, auto.TextUnit.Character,
                    -self._context_chars,
                )
                expanded.MoveEndpointByUnit(
                    auto.TextPatternRangeEndpoint.End, auto.TextUnit.Character,
                    self._context_chars,
                )
                full = expanded.GetText(-1) or ""
                idx = full.find(selected)
                if idx >= 0:
                    before = full[:idx]
                    after = full[idx + len(selected):]
            except Exception as exc:
                log.debug("fallback context expansion failed (%s)", type(exc).__name__)

        return before, after
