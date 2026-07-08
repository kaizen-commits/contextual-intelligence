"""Background Qt worker for UIA capture and LLM streaming."""

from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtCore import QThread, Signal
from openai import APIConnectionError
from pydantic import ValidationError

from contextual_intelligence.capture import CaptureOrchestrator
from contextual_intelligence.clipboard import read_text_clipboard
from contextual_intelligence.llm import LlmClient
from contextual_intelligence.models import (
    CaptureError,
    CaptureTier,
    ContextPayload,
    MAX_LOOKUP_CHARS,
    RECENT_COPY_TTL_SECONDS,
    RecentAppCopy,
)

log = logging.getLogger(__name__)

# gemma-4 occasionally streams an empty/whitespace-only response and an
# immediate retry succeeds; retry once before surfacing the error card.
_EMPTY_RESPONSE_RETRIES = 1

_CAPTURE_FAILED_GUIDANCE = (
    "Lookup needs an active selection. Select a word, or copy a short "
    "word/phrase from the Smart Paste result and try again."
)


class LookupWorker(QThread):
    """Runs UIA capture and LM Studio streaming on a background thread so the
    GUI event loop never blocks."""

    started_capture = Signal()
    capture_succeeded = Signal(object)  # ContextPayload
    token_received = Signal(str)
    finished_lookup = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        orchestrator: CaptureOrchestrator,
        llm_client: LlmClient,
        parent: Any | None = None,
        recent_copy: RecentAppCopy | None = None,
    ) -> None:
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._llm_client = llm_client
        self._recent_copy = recent_copy
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _recent_copy_payload(self) -> ContextPayload | None:
        """Handoff for text the app itself just copied (SCOPE-30).

        Never a general clipboard fallback: requires a recorded in-app copy,
        within its freshness window, still exactly matching the clipboard.
        """
        rc = self._recent_copy
        if rc is None:
            return None
        age = time.monotonic() - rc.copied_at
        if age > RECENT_COPY_TTL_SECONDS:
            log.info("recent %s copy is stale (%.0fs old); not using handoff", rc.source, age)
            return None
        clipboard_text = (read_text_clipboard() or "").replace("\r\n", "\n").strip()
        if clipboard_text != rc.text.replace("\r\n", "\n").strip():
            log.info("clipboard no longer matches recent %s copy; not using handoff", rc.source)
            return None
        try:
            return ContextPayload(
                selected_text=rc.text,
                app_name="Smart Paste result",
                tier=CaptureTier.CLIPBOARD,
            )
        except ValidationError as exc:
            log.info("recent %s copy failed payload validation: %s", rc.source, exc)
            return None

    def run(self) -> None:
        self._cancelled = False
        t_start = time.perf_counter()
        self.started_capture.emit()

        try:
            payload = self._orchestrator.capture()
        except CaptureError as exc:
            log.warning("capture failed after %.2fs: %s", time.perf_counter() - t_start, exc)
            payload = self._recent_copy_payload()
            if payload is None:
                self.error_occurred.emit(_CAPTURE_FAILED_GUIDANCE)
                return
            log.info(
                "all capture tiers failed; using recent Smart Paste copy handoff (%d chars)",
                len(payload.selected_text),
            )
        except Exception as exc:
            log.exception("unexpected error during capture")
            self.error_occurred.emit(f"Unexpected error: {exc}")
            return

        t_captured = time.perf_counter()
        if self._cancelled:
            return

        self.capture_succeeded.emit(payload)

        if len(payload.selected_text) > MAX_LOOKUP_CHARS:
            log.info(
                "selection exceeds MAX_LOOKUP_CHARS (%d > %d); skipping LLM lookup and displaying limitation",
                len(payload.selected_text),
                MAX_LOOKUP_CHARS,
            )
            self.finished_lookup.emit()
            return

        t_first_token: float | None = None
        n_chars = 0
        attempt = 0
        try:
            while True:
                attempt += 1
                got_visible = False
                for chunk in self._llm_client.stream_lookup(payload):
                    if t_first_token is None:
                        t_first_token = time.perf_counter()
                    if self._cancelled:
                        break
                    n_chars += len(chunk)
                    if chunk.strip():
                        got_visible = True
                    self.token_received.emit(chunk)
                if got_visible or self._cancelled or attempt > _EMPTY_RESPONSE_RETRIES:
                    break
                log.warning(
                    "model returned empty response, retrying (attempt %d of %d)",
                    attempt + 1, _EMPTY_RESPONSE_RETRIES + 1,
                )
        except APIConnectionError:
            log.error("cannot reach LM Studio")
            self.error_occurred.emit(
                "Cannot reach LM Studio — is the server running? "
                "(Check Developer tab -> Start Server)"
            )
            return
        except Exception as exc:
            log.exception("error during LLM streaming")
            self.error_occurred.emit(f"LM Studio error: {exc}")
            return

        t_end = time.perf_counter()
        log.info(
            "lookup lifecycle: tier=%s app=%s chars=%d capture=%.2fs first_token=+%.2fs "
            "stream=%.2fs total=%.2fs%s",
            payload.tier,
            payload.app_name or "?",
            n_chars,
            t_captured - t_start,
            (t_first_token - t_captured) if t_first_token is not None else -1.0,
            (t_end - t_first_token) if t_first_token is not None else 0.0,
            t_end - t_start,
            (f" attempts={attempt}" if attempt > 1 else "")
            + (" (cancelled)" if self._cancelled else ""),
        )
        if not self._cancelled:
            self.finished_lookup.emit()
