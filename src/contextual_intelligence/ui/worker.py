"""Background Qt worker for UIA capture and LLM streaming."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QThread, Signal
from openai import APIConnectionError

from contextual_intelligence.capture import CaptureOrchestrator
from contextual_intelligence.llm import LlmClient
from contextual_intelligence.models import CaptureError

log = logging.getLogger(__name__)


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
    ) -> None:
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._llm_client = llm_client
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        self._cancelled = False
        self.started_capture.emit()

        try:
            payload = self._orchestrator.capture()
        except CaptureError as exc:
            log.warning("capture failed: %s", exc)
            self.error_occurred.emit(f"Capture failed: {exc.reason}")
            return
        except Exception as exc:
            log.exception("unexpected error during capture")
            self.error_occurred.emit(f"Unexpected error: {exc}")
            return

        if self._cancelled:
            return

        self.capture_succeeded.emit(payload)

        try:
            for chunk in self._llm_client.stream_lookup(payload):
                if self._cancelled:
                    break
                self.token_received.emit(chunk)
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

        if not self._cancelled:
            self.finished_lookup.emit()
