"""Phase-aware clipboard locker for manual QA (dev-only).

Exercises the *restoration-failure* path of the clipboard fallback: it waits
for the clipboard sequence number to change (i.e. until AFTER the fallback's
synthetic Ctrl+C lands) and only then opens and holds the clipboard, so the
fallback's restore write fails after retries and must surface
CaptureIntegrityError. A locker held from the very start would only exercise
snapshot UNAVAILABLE, which is a different (pre-mutation) branch.

Usage (from the repo root, with the tray app running and fallback enabled):
    uv run python scripts/qa/clipboard_locker.py [hold-seconds]

Then trigger a fallback capture (Ctrl+Alt+D in an app without UIA TextPattern)
within 30 seconds. Expected: the popup shows the clipboard-restoration-failed
guidance, and the log contains the restore FAILED entry.
"""

from __future__ import annotations

import ctypes
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)

ARM_TIMEOUT_S = 30.0
DEFAULT_HOLD_S = 5.0


def main() -> int:
    hold_s = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_HOLD_S
    baseline = user32.GetClipboardSequenceNumber()
    print(f"armed: waiting up to {ARM_TIMEOUT_S:.0f}s for a clipboard change "
          f"(sequence {baseline}) — trigger the fallback capture now")

    deadline = time.monotonic() + ARM_TIMEOUT_S
    while user32.GetClipboardSequenceNumber() == baseline:
        if time.monotonic() > deadline:
            print("timed out: no clipboard change observed")
            return 1
        time.sleep(0.002)

    # The synthetic copy just landed; grab the clipboard before the fallback's
    # restore can (its write retries 5 x 20ms, so holding longer guarantees
    # the FAILED path).
    if not user32.OpenClipboard(None):
        print("could not open clipboard fast enough; re-run and retry")
        return 1
    try:
        print(f"clipboard LOCKED for {hold_s:.1f}s — restore must fail now")
        time.sleep(hold_s)
    finally:
        user32.CloseClipboard()
    print("released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
