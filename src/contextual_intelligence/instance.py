"""Single-instance guard via a named Win32 mutex.

A convenience guard, not a security boundary: an unexpected CreateMutexW
failure fails OPEN (the app runs unguarded for the session) because refusing
to start over an exotic API failure would be worse than the duplicate-instance
UX the guard exists to prevent (hardening pass Rev 3, P7).
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\ContextualIntelligence.Tray"
_ERROR_ALREADY_EXISTS = 183

# use_last_error=True captures GetLastError at call time; reading it later via
# windll would be stale (any intervening Python call can clobber it).
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

# Held for process lifetime once acquired; release_instance_lock() closes it on
# clean shutdown, and the OS reclaims it on any exit path (including os._exit).
_mutex_handle: int | None = None


def acquire_single_instance_lock() -> bool:
    """True when this process holds the single-instance lock (or the guard is
    unavailable — fail open); False when another instance already holds it."""
    global _mutex_handle
    handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    last_error = ctypes.get_last_error()
    if not handle:
        log.warning(
            "single-instance guard unavailable (CreateMutexW failed, error %d); "
            "continuing without it",
            last_error,
        )
        return True
    if last_error == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)  # duplicate handle to the other instance's mutex
        return False
    _mutex_handle = handle
    return True


def release_instance_lock() -> None:
    """Close the owned mutex handle (clean-shutdown path); idempotent."""
    global _mutex_handle
    if _mutex_handle is not None:
        _kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
