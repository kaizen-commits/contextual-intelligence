"""Minimal global hotkey via RegisterHotKey + message loop.

Phase 0 keeps this deliberately tiny: one hotkey, one callback, blocking
loop. The PySide6-integrated version arrives with the popup in Phase 1.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_REMOVE = 0x0001

_HOTKEY_ID = 1


class HotkeyError(RuntimeError):
    pass


def run_hotkey_loop(callback: Callable[[], None], vk: int = ord("D")) -> None:
    """Register Ctrl+Alt+<vk> and invoke callback per press. Blocks forever
    (Ctrl+C to stop). Raises HotkeyError if registration fails — usually a
    conflict with another app."""
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, _HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
        raise HotkeyError(
            f"RegisterHotKey failed for Ctrl+Alt+{chr(vk)} — "
            "another app may own this hotkey"
        )
    log.info("hotkey registered: Ctrl+Alt+%s (Ctrl+C in this console to quit)", chr(vk))
    try:
        msg = wintypes.MSG()
        while True:
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_QUIT:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    try:
                        callback()
                    except Exception:
                        log.exception("lookup failed")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.02)
    finally:
        user32.UnregisterHotKey(None, _HOTKEY_ID)
