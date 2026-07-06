"""Minimal global hotkey via RegisterHotKey + message loop.

Phase 0 keeps this deliberately tiny: one hotkey, one callback, blocking
loop. The PySide6-integrated version arrives with the popup in Phase 1.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_HOTKEY_ID = 1


class HotkeyError(RuntimeError):
    pass


def run_hotkey_loop(
    callback: Callable[[], None],
    vk: int = ord("D"),
    on_thread_id: Callable[[int], None] | None = None,
) -> None:
    """Register Ctrl+Alt+<vk> and invoke callback per press. Blocks forever
    (WM_QUIT to stop). Raises HotkeyError if registration fails — usually a
    conflict with another app."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.RegisterHotKey(None, _HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
        raise HotkeyError(
            f"RegisterHotKey failed for Ctrl+Alt+{chr(vk)} — "
            "another app may own this hotkey"
        )
    log.info("hotkey registered: Ctrl+Alt+%s", chr(vk))

    if on_thread_id is not None:
        on_thread_id(kernel32.GetCurrentThreadId())

    try:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                try:
                    callback()
                except Exception:
                    log.exception("lookup failed")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnregisterHotKey(None, _HOTKEY_ID)
