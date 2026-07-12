"""Global hotkey registration and message loop.

Supports registering multiple hotkeys on a single message loop thread with
degradation semantics: if one hotkey fails to register (e.g., due to a conflict),
other hotkeys continue to function.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000

# Standard hotkey IDs used across the application
LOOKUP_HOTKEY_ID = 1
PASTE_HOTKEY_ID = 2


class HotkeyError(RuntimeError):
    pass


def run_hotkey_loop(
    callback: Callable[[], None] | None = None,
    vk: int = ord("D"),
    on_thread_id: Callable[[int], None] | None = None,
    hotkey_map: dict[int, tuple[int, Callable[[], None]]] | None = None,
    on_registration_failure: Callable[[int, int], None] | None = None,
    on_ready: Callable[[], None] | None = None,
    stopping: threading.Event | None = None,
) -> None:
    """Register hotkeys and process WM_HOTKEY messages on the calling thread.

    Can be called either in legacy single-hotkey mode via `callback` and `vk`,
    or in multi-hotkey mode via `hotkey_map` which maps hotkey_id -> (vk, callback).

    If multiple hotkeys are provided and at least one registers successfully,
    the message loop runs (degradation semantics). If ALL hotkeys fail to register,
    raises HotkeyError.

    Shutdown handshake: the message queue is created explicitly before
    `on_thread_id`/`on_ready` fire, so a caller that has seen `on_ready` may
    safely PostThreadMessageW(WM_QUIT) — posting to a thread without a queue
    fails. `stopping` is checked once between readiness and the blocking
    GetMessageW loop, closing the race where stop is requested before the
    loop starts.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if hotkey_map is None:
        if callback is None:
            raise ValueError("Must provide either callback or hotkey_map")
        hotkey_map = {LOOKUP_HOTKEY_ID: (vk, callback)}

    msg = wintypes.MSG()
    # Force creation of this thread's message queue before anyone can learn
    # our thread id; RegisterHotKey alone does not guarantee it exists yet.
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)

    registered_ids: list[int] = []

    for hotkey_id, (vk_code, cb) in hotkey_map.items():
        if user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk_code):
            log.info("hotkey registered [id=%d]: Ctrl+Alt+%s", hotkey_id, chr(vk_code))
            registered_ids.append(hotkey_id)
        else:
            log.warning(
                "RegisterHotKey failed for [id=%d] Ctrl+Alt+%s — another app may own this hotkey",
                hotkey_id,
                chr(vk_code),
            )
            if on_registration_failure is not None:
                on_registration_failure(hotkey_id, vk_code)

    try:
        if not registered_ids:
            raise HotkeyError("All hotkeys failed to register — another app may own them")

        if on_thread_id is not None:
            on_thread_id(kernel32.GetCurrentThreadId())
        if on_ready is not None:
            on_ready()
        if stopping is not None and stopping.is_set():
            return

        while True:
            rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if rc == 0:  # WM_QUIT
                break
            if rc == -1:  # error: MSG is not valid, must not be dispatched
                log.error(
                    "GetMessageW failed (error %d); stopping hotkey loop",
                    kernel32.GetLastError(),
                )
                break
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                if hotkey_id in hotkey_map:
                    try:
                        _, cb = hotkey_map[hotkey_id]
                        cb()
                    except Exception:
                        log.exception("hotkey callback failed for id=%d", hotkey_id)
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        for hotkey_id in registered_ids:
            user32.UnregisterHotKey(None, hotkey_id)
