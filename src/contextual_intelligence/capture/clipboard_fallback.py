"""Fallback capture tier: deterministic clipboard automation. STUB.

The old app died from listener ghosting: click/keyboard listeners kept
running after the popup closed and misbehaved around right-click and
triple-click. This stub locks in the lifecycle contract *before* any
automation code lands, so the real implementation (Phase 1+, only where
UIA telemetry proves it is needed) inherits the rules instead of
rediscovering the bug:

- one active capture at a time
- explicit arm/disarm transitions; capture is only legal while armed
- capture always disarms on exit, success or failure
- disarm is idempotent and legal from any state (cancellation path)

The eventual implementation: save clipboard -> SendInput Ctrl+C -> verify
via clipboard sequence-number polling (WM_COPY can report success without
updating the clipboard in Electron/Chromium) -> read selection -> restore
clipboard with retry.
"""

from __future__ import annotations

import logging
import threading
from enum import StrEnum

from contextual_intelligence.models import CaptureError, CaptureTier, ContextPayload

log = logging.getLogger(__name__)


class ListenerState(StrEnum):
    DISARMED = "disarmed"
    ARMED = "armed"
    CAPTURING = "capturing"


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
            raise CaptureError(
                "clipboard fallback not implemented (stub; graduates in Phase 1+ "
                "only where UIA telemetry proves it is needed)",
                CaptureTier.CLIPBOARD,
            )
        finally:
            self.disarm()


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
