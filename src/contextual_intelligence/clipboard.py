"""Public clipboard utilities for Contextual Intelligence.

Provides safe reading, writing, and format inspection with retry-and-backoff
discipline to survive transient clipboard locks from other applications.
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass

import win32clipboard
import win32con

from contextual_intelligence.models import RestoreOutcome, SnapshotStatus

log = logging.getLogger(__name__)

# High-value non-text clipboard formats that we must never overwrite or clobber.
# Note: Excel and Word cell copies may include CF_BITMAP alongside text; per Phase 2 MVP rules,
# we conservatively abort on these high-value formats. We proceed when Unicode text is present
# alongside HTML/RTF metadata.
_HIGH_VALUE_NON_TEXT_FORMATS = {
    win32con.CF_BITMAP,
    win32con.CF_DIB,
    win32con.CF_DIBV5,
    win32con.CF_HDROP,
    win32con.CF_WAVE,
    win32con.CF_RIFF,
}


@dataclass
class ClipboardSnapshot:
    status: SnapshotStatus
    text: str | None = None
    sequence: int = 0


def has_high_value_non_text_format() -> bool:
    """Check if the clipboard holds high-value non-text formats (images, files, audio).

    Returns True if high-value formats are detected OR if the clipboard cannot be opened
    after retries (fail-closed behavior to protect user data).
    """
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                fmt = 0
                while True:
                    fmt = win32clipboard.EnumClipboardFormats(fmt)
                    if fmt == 0:
                        break
                    if fmt in _HIGH_VALUE_NON_TEXT_FORMATS:
                        return True
                return False
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.02)
    # Fail closed: if we cannot open the clipboard after retries, assume it has non-text
    return True


def read_text_clipboard() -> str | None:
    """Read Unicode text from the clipboard with retry-and-backoff discipline.

    Returns the string if text is available, or None if empty or locked.
    """
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                    data = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                    return (
                        data.decode("utf-8", errors="replace")
                        if isinstance(data, bytes)
                        else str(data)
                    )
                return None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.02)
    return None


def write_text_clipboard(text: str) -> bool:
    """Write Unicode text to the clipboard with retry-and-backoff discipline.

    Returns True on success, or False if the clipboard remained locked across all retries.
    Does not raise exceptions, ensuring safe usage in cleanup blocks and UI handlers.
    """
    if text is None:
        return False
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception as exc:
            log.debug("clipboard write failed, retrying: %s", exc)
            time.sleep(0.02)
    log.error("failed to write to clipboard after 5 retries")
    return False


def snapshot_clipboard() -> ClipboardSnapshot:
    """Enumerate formats and snapshot the clipboard in a single OpenClipboard session.

    Returns a ClipboardSnapshot containing status, sequence number, and Unicode text.
    """
    user32 = ctypes.windll.user32
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                seq = user32.GetClipboardSequenceNumber()

                fmt = 0
                formats = []
                while True:
                    fmt = win32clipboard.EnumClipboardFormats(fmt)
                    if fmt == 0:
                        break
                    formats.append(fmt)

                if not formats:
                    return ClipboardSnapshot(status=SnapshotStatus.EMPTY, sequence=seq)

                unsupported = False
                for f in formats:
                    if f in _HIGH_VALUE_NON_TEXT_FORMATS:
                        unsupported = True
                        break

                if unsupported:
                    return ClipboardSnapshot(status=SnapshotStatus.UNSUPPORTED, sequence=seq)

                text = None
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                    data = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                    text = (
                        data.decode("utf-8", errors="replace")
                        if isinstance(data, bytes)
                        else str(data)
                    )

                if text is not None:
                    return ClipboardSnapshot(status=SnapshotStatus.TEXT, text=text, sequence=seq)

                return ClipboardSnapshot(status=SnapshotStatus.UNSUPPORTED, sequence=seq)
            finally:
                win32clipboard.CloseClipboard()
        except Exception as exc:
            log.debug("clipboard snapshot failed, retrying: %s", exc)
            time.sleep(0.02)
    return ClipboardSnapshot(status=SnapshotStatus.UNAVAILABLE)


def restore_clipboard_if_owned(snapshot: ClipboardSnapshot, owned_seq: int | None) -> RestoreOutcome:
    """Single conditional-restore operation under a single OpenClipboard acquisition."""
    if owned_seq is None:
        return RestoreOutcome.NO_OWNERSHIP

    user32 = ctypes.windll.user32
    opened = False
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            opened = True
            break
        except Exception:
            time.sleep(0.02)

    if not opened:
        return RestoreOutcome.FAILED

    try:
        current_seq = user32.GetClipboardSequenceNumber()
        if current_seq != owned_seq:
            return RestoreOutcome.EXTERNAL_CHANGE

        try:
            win32clipboard.EmptyClipboard()
        except Exception as exc:
            log.error("EmptyClipboard failed: %s", exc)
            return RestoreOutcome.FAILED

        if snapshot.status == SnapshotStatus.EMPTY:
            return RestoreOutcome.RESTORED

        if snapshot.status == SnapshotStatus.TEXT and snapshot.text is not None:
            try:
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, snapshot.text)
                return RestoreOutcome.RESTORED
            except Exception as exc:
                log.error("SetClipboardData failed after EmptyClipboard: %s", exc)
                return RestoreOutcome.FAILED_CLEARED

        return RestoreOutcome.RESTORED
    finally:
        win32clipboard.CloseClipboard()
