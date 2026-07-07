"""Background Qt worker for Smart Paste LLM streaming."""

from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtCore import QThread, Signal
from openai import APIConnectionError

from contextual_intelligence.llm import LlmClient
from contextual_intelligence.models import PastePayload

log = logging.getLogger(__name__)

# gemma-4 occasionally streams an empty/whitespace-only response and an
# immediate retry succeeds; retry once before surfacing the error.
_EMPTY_RESPONSE_RETRIES = 1


class PasteWorker(QThread):
    """Runs Smart Paste LM Studio streaming on a background thread so the
    GUI event loop never blocks."""

    started_transform = Signal()
    retrying_transform = Signal(int)  # attempt number
    token_received = Signal(str)
    finished_transform = Signal(str, float)  # transformed_text, duration_ms
    error_occurred = Signal(str)

    def __init__(
        self,
        payload: PastePayload,
        llm_client: LlmClient,
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._payload = payload
        self._llm_client = llm_client
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        self._cancelled = False
        t_start = time.perf_counter()
        self.started_transform.emit()

        t_first_token: float | None = None
        transformed_text = ""
        attempt = 0
        try:
            while True:
                attempt += 1
                transformed_text = ""
                got_visible = False
                for chunk in self._llm_client.stream_transform(self._payload):
                    if t_first_token is None:
                        t_first_token = time.perf_counter()
                    if self._cancelled:
                        break
                    transformed_text += chunk
                    if chunk.strip():
                        got_visible = True
                    self.token_received.emit(chunk)
                if got_visible or self._cancelled or attempt > _EMPTY_RESPONSE_RETRIES:
                    break
                log.warning(
                    "model returned empty response for paste transform, retrying (attempt %d of %d)",
                    attempt + 1, _EMPTY_RESPONSE_RETRIES + 1,
                )
                self.retrying_transform.emit(attempt + 1)
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
        duration_ms = (t_end - t_start) * 1000
        log.info(
            "transform lifecycle: app=%s input_chars=%d output_chars=%d first_token=%.2fs "
            "stream=%.2fs total=%.2fs%s",
            self._payload.app_name or "?",
            len(self._payload.text),
            len(transformed_text),
            (t_first_token - t_start) if t_first_token is not None else -1.0,
            (t_end - t_first_token) if t_first_token is not None else 0.0,
            t_end - t_start,
            (f" attempts={attempt}" if attempt > 1 else "")
            + (" (cancelled)" if self._cancelled else ""),
        )
        if not self._cancelled:
            self.finished_transform.emit(transformed_text.strip(), duration_ms)
