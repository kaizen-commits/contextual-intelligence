"""Background Qt worker for UIA capture and LLM streaming."""

from __future__ import annotations

import logging
import threading
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
    ProtectedFieldError,
    CaptureIntegrityError,
    RestoreFailureFlavor,
)

log = logging.getLogger(__name__)

# gemma-4 occasionally streams an empty/whitespace-only response and an
# immediate retry succeeds; retry once before surfacing the error card.
_EMPTY_RESPONSE_RETRIES = 1

_CAPTURE_FAILED_GUIDANCE = (
    "Lookup needs an active selection. Select a word, or copy a short "
    "word/phrase from the Smart Paste result and try again."
)
_CAPTURE_UNEXPECTED_GUIDANCE = "Lookup failed during capture. Try selecting the text again or restarting the app."
_MODEL_ERROR_GUIDANCE = "The local model returned an error. Check LM Studio and try again."


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
        prefer_recent_copy: bool = False,
        fallback_enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._llm_client = llm_client
        self._recent_copy = recent_copy
        self._prefer_recent_copy = prefer_recent_copy
        self._fallback_enabled = fallback_enabled
        # An Event, never reset inside run(): a cancel issued between start()
        # and the thread's first instruction must stick.
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def was_cancelled(self) -> bool:
        return self._cancel.is_set()

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
        except ValidationError:
            log.info("recent %s copy failed payload validation", rc.source)
            return None

    def run(self) -> None:
        # Cancellation checkpoint: before any signal, capture, or LLM work — a
        # pre-cancelled worker must make zero orchestrator and zero LLM calls.
        if self._cancel.is_set():
            return
        t_start = time.perf_counter()
        self.started_capture.emit()

        payload = None
        if self._prefer_recent_copy:
            payload = self._recent_copy_payload()
            if payload is not None:
                log.info(
                    "lookup triggered from palette; using recent Smart Paste copy directly (%d chars)",
                    len(payload.selected_text),
                )

        if payload is None:
            if self._cancel.is_set():  # checkpoint: before capture
                return
            try:
                payload = self._orchestrator.capture()
            except ProtectedFieldError as exc:
                log.warning("protected field capture blocked after %.2fs: %s", time.perf_counter() - t_start, exc)
                self.error_occurred.emit("Lookup is disabled in password and protected fields for your privacy.")
                return
            except CaptureIntegrityError as exc:
                log.warning("capture integrity error after %.2fs: %s", time.perf_counter() - t_start, exc)
                if exc.flavor == RestoreFailureFlavor.CLEARED:
                    self.error_occurred.emit(
                        "Clipboard restoration failed. Your original clipboard text could not be "
                        "put back and the clipboard has been cleared."
                    )
                else:
                    self.error_occurred.emit(
                        "Clipboard restoration failed. The selected text may still be on your clipboard. "
                        "Copy something else or clear it (Win+V -> Clear all) before continuing."
                    )
                return
            except CaptureError as exc:
                log.warning("capture failed after %.2fs: %s", time.perf_counter() - t_start, exc)
                payload = self._recent_copy_payload()
                if payload is None:
                    if self._fallback_enabled:
                        self.error_occurred.emit(_CAPTURE_FAILED_GUIDANCE)
                    else:
                        self.error_occurred.emit(
                            "Lookup needs an active selection, and this app doesn't expose one to "
                            "Windows accessibility. You can enable the clipboard-fallback capture in config.toml "
                            "(see README: Clipboard fallback) — it briefly copies your selection."
                        )
                    return
                log.info(
                    "all capture tiers failed; using recent Smart Paste copy handoff (%d chars)",
                    len(payload.selected_text),
                )
            except Exception:
                log.error("unexpected error during capture")
                self.error_occurred.emit(_CAPTURE_UNEXPECTED_GUIDANCE)
                return

        t_captured = time.perf_counter()
        if self._cancel.is_set():
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
                if self._cancel.is_set():  # checkpoint: before each stream creation/retry
                    break
                got_visible = False
                for chunk in self._llm_client.stream_lookup(payload):
                    if t_first_token is None:
                        t_first_token = time.perf_counter()
                    if self._cancel.is_set():
                        break
                    n_chars += len(chunk)
                    if chunk.strip():
                        got_visible = True
                    self.token_received.emit(chunk)
                if got_visible or self._cancel.is_set() or attempt > _EMPTY_RESPONSE_RETRIES:
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
        except Exception:
            log.error("error during LLM streaming")
            self.error_occurred.emit(_MODEL_ERROR_GUIDANCE)
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
            + (" (cancelled)" if self._cancel.is_set() else ""),
        )
        if not self._cancel.is_set():
            self.finished_lookup.emit()
