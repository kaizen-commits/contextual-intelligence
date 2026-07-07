"""Capture providers and the tier orchestrator.

Tier order is the product decision (accessibility-first): UIA TextPattern is
primary; clipboard automation is a fallback that only graduates from stub to
implementation with app-matrix evidence; OCR is deferred entirely.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from pydantic import ValidationError

from contextual_intelligence.capture.uia import get_foreground_app_name, get_process_image_name
from contextual_intelligence.models import CaptureError, ContextPayload

__all__ = [
    "CaptureProvider",
    "CaptureAttempt",
    "CaptureOrchestrator",
    "get_foreground_app_name",
    "get_process_image_name",
]

log = logging.getLogger(__name__)


@runtime_checkable
class CaptureProvider(Protocol):
    name: str

    def capture(self) -> ContextPayload: ...


@dataclass
class CaptureAttempt:
    provider: str
    ok: bool
    duration_ms: float
    error: str | None = None


class CaptureOrchestrator:
    """Try providers in order; return the first validated payload.

    Every attempt is logged with tier, duration, and failure reason —
    this telemetry decides which fallback work is actually needed.
    """

    def __init__(self, providers: Sequence[CaptureProvider]):
        if not providers:
            raise ValueError("at least one capture provider required")
        self._providers = list(providers)
        self.last_attempts: list[CaptureAttempt] = []

    def capture(self) -> ContextPayload:
        self.last_attempts = []
        for provider in self._providers:
            start = time.perf_counter()
            try:
                payload = provider.capture()
            except (CaptureError, ValidationError) as exc:
                ms = (time.perf_counter() - start) * 1000
                reason = exc.reason if isinstance(exc, CaptureError) else f"validation error: {exc}"
                self.last_attempts.append(CaptureAttempt(provider.name, False, ms, reason))
                log.info("capture tier=%s ok=False ms=%.0f reason=%s", provider.name, ms, reason)
                continue
            ms = (time.perf_counter() - start) * 1000
            self.last_attempts.append(CaptureAttempt(provider.name, True, ms))
            log.info(
                "capture tier=%s ok=True ms=%.0f app=%s selection_len=%d context=%s",
                provider.name, ms, payload.app_name, len(payload.selected_text),
                payload.has_context,
            )
            return payload
        reasons = "; ".join(f"{a.provider}: {a.error}" for a in self.last_attempts)
        raise CaptureError(f"all capture tiers failed ({reasons})")
