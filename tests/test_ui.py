import time

import pytest
from PySide6.QtWidgets import QApplication

from contextual_intelligence.models import (
    CaptureError,
    CaptureTier,
    ContextPayload,
    RecentAppCopy,
)
from contextual_intelligence.ui.popup import LookupPopupWindow
from contextual_intelligence.ui.worker import LookupWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class MockOrchestrator:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def capture(self):
        if self._error:
            raise self._error
        return self._payload or ContextPayload(
            selected_text="word",
            tier=CaptureTier.UIA,
            app_name="Notepad",
        )


class MockLlmClient:
    def __init__(self, tokens=None, error=None):
        self._tokens = tokens or ["word (noun)\n", "Definition line.\n", "Context: [Test]\n", "Synonyms: none"]
        self._error = error

    def stream_lookup(self, payload):
        if self._error:
            raise self._error
        for t in self._tokens:
            yield t


def test_popup_window_formatting(qapp):
    popup = LookupPopupWindow()
    assert popup.status_label.text() == "⏳ Analyzing context..."
    assert popup.title_label.isHidden()
    assert popup.def_label.isHidden()

    popup._on_token("test (noun)\nA simple test.")
    assert not popup.title_label.isHidden()
    assert popup.title_label.text() == "test (noun)"
    assert not popup.def_label.isHidden()
    assert popup.def_label.text() == "A simple test."
    assert popup.ctx_label.isHidden()

    popup._on_token("\nContext: [Code] in unit tests.\nSynonyms: check, verify")
    assert not popup.ctx_label.isHidden()
    assert popup.ctx_label.text() == "Context: [Code] in unit tests."
    assert not popup.syn_label.isHidden()
    assert popup.syn_label.text() == "Synonyms: check, verify"


def test_popup_empty_response_shows_message(qapp):
    """A whitespace-only or empty model stream must not render a blank card."""
    popup = LookupPopupWindow()

    # Whitespace-only tokens must not hide the status label
    popup._on_token("\n")
    popup._on_token("  \n")
    assert not popup.status_label.isHidden()
    assert popup.title_label.isHidden()

    popup._on_finished()
    assert not popup.status_label.isHidden()
    assert "empty response" in popup.status_label.text()


def test_popup_empty_response_informative_guidance(qapp):
    popup_short = LookupPopupWindow()
    popup_short._on_capture_succeeded(ContextPayload(selected_text="short word", tier=CaptureTier.UIA, app_name="test"))
    popup_short._on_finished()
    assert "up to 150 chars" in popup_short.status_label.text()
    assert "re-select a specific term" in popup_short.status_label.text()

    popup_long = LookupPopupWindow()
    popup_long._on_capture_succeeded(ContextPayload(selected_text="x" * 500, tier=CaptureTier.UIA, app_name="test"))
    popup_long._on_finished()
    assert "You selected 500 chars" in popup_long.status_label.text()
    assert "use Smart Paste (Ctrl+Alt+V)" in popup_long.status_label.text()


def test_popup_finished_with_content_hides_status(qapp):
    popup = LookupPopupWindow()
    popup._on_token("test (noun)\nA simple test.")
    popup._on_finished()
    assert popup.status_label.isHidden()
    assert not popup.title_label.isHidden()


def test_popup_window_error_state(qapp):
    popup = LookupPopupWindow()
    popup._on_error("Capture failed: empty selection")
    assert "❌ Capture failed: empty selection" in popup.status_label.text()
    assert popup.title_label.isHidden()


def test_popup_error_message_not_clipped(qapp):
    """Long wrapped status messages must grow the popup, not clip (SCOPE-30 QA)."""
    popup = LookupPopupWindow()
    popup.show()
    popup._on_started()  # compact size, as in the real trigger sequence
    popup._on_error("Lookup needs an active selection. " * 10)
    qapp.processEvents()

    label = popup.status_label
    assert label.height() >= label.heightForWidth(label.width())
    assert popup.height() <= popup.maximumHeight()
    popup.close()


def test_popup_window_dimensions_and_truncation(qapp):
    popup = LookupPopupWindow()
    assert popup.maximumHeight() == 350
    long_payload = ContextPayload(
        selected_text="When it comes to the question of what your heart rate means, psychologically speaking, the scientifically correct answer is: it depends. " * 5,
        tier=CaptureTier.UIA,
        app_name="chrome.exe",
    )
    popup._on_capture_succeeded(long_payload)
    assert len(popup.status_label.text()) < 150
    assert "..." in popup.status_label.text()
    assert popup.height() <= 350


def test_lookup_worker_success(qapp):
    orch = MockOrchestrator()
    llm = MockLlmClient(tokens=["hello ", "world"])
    worker = LookupWorker(orch, llm)

    events = []
    worker.started_capture.connect(lambda: events.append("started"))
    worker.capture_succeeded.connect(lambda p: events.append(f"captured:{p.selected_text}"))
    worker.token_received.connect(lambda t: events.append(f"token:{t}"))
    worker.finished_lookup.connect(lambda: events.append("finished"))

    worker.run()
    assert events == [
        "started",
        "captured:word",
        "token:hello ",
        "token:world",
        "finished",
    ]


class SequencedLlmClient:
    """Returns a different response per stream_lookup call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def stream_lookup(self, payload):
        self.calls += 1
        yield from self._responses.pop(0)


def test_lookup_worker_retries_empty_response(qapp):
    orch = MockOrchestrator()
    llm = SequencedLlmClient([["\n", "  "], ["word (noun)\n", "A definition."]])
    worker = LookupWorker(orch, llm)

    tokens = []
    finished = []
    worker.token_received.connect(tokens.append)
    worker.finished_lookup.connect(lambda: finished.append(True))

    worker.run()
    assert llm.calls == 2
    assert "word (noun)" in "".join(tokens)
    assert finished


def test_lookup_worker_gives_up_after_retry(qapp):
    orch = MockOrchestrator()
    llm = SequencedLlmClient([["\n"], ["  "]])
    worker = LookupWorker(orch, llm)

    finished = []
    worker.finished_lookup.connect(lambda: finished.append(True))

    worker.run()
    # Retried once, then finished normally — the popup's empty-buffer
    # guard is what surfaces the error to the user.
    assert llm.calls == 2
    assert finished


def test_lookup_worker_capture_error_shows_guidance(qapp):
    """Raw capture failure reasons are logged, not shown; the user gets guidance (SCOPE-30)."""
    orch = MockOrchestrator(error=CaptureError("no selection", CaptureTier.UIA))
    llm = MockLlmClient()
    worker = LookupWorker(orch, llm)

    errors = []
    worker.error_occurred.connect(lambda msg: errors.append(msg))
    worker.run()
    assert len(errors) == 1
    assert "Lookup needs an active selection" in errors[0]
    assert "no selection" not in errors[0]


def test_lookup_worker_no_arbitrary_clipboard_fallback(qapp, monkeypatch):
    """Clipboard contents alone must never become lookup input (SCOPE-30)."""
    monkeypatch.setattr(
        "contextual_intelligence.ui.worker.read_text_clipboard", lambda: "stale clipboard text"
    )
    orch = MockOrchestrator(error=CaptureError("no selection", CaptureTier.UIA))
    worker = LookupWorker(orch, MockLlmClient())  # no recent_copy recorded

    captured, errors = [], []
    worker.capture_succeeded.connect(captured.append)
    worker.error_occurred.connect(errors.append)
    worker.run()
    assert not captured
    assert len(errors) == 1


def test_lookup_worker_recent_copy_handoff(qapp, monkeypatch):
    """A fresh in-app copy still matching the clipboard is used after all tiers fail (SCOPE-30)."""
    monkeypatch.setattr("contextual_intelligence.ui.worker.read_text_clipboard", lambda: "gadget")
    orch = MockOrchestrator(error=CaptureError("all capture tiers failed", CaptureTier.CLIPBOARD))
    recent = RecentAppCopy(text="gadget", copied_at=time.monotonic(), source="smart_paste")
    worker = LookupWorker(
        orch, MockLlmClient(tokens=["gadget (noun)\n", "A device."]), recent_copy=recent
    )

    events = []
    worker.capture_succeeded.connect(lambda p: events.append(f"captured:{p.selected_text}:{p.app_name}"))
    worker.finished_lookup.connect(lambda: events.append("finished"))
    worker.error_occurred.connect(lambda m: events.append(f"error:{m}"))
    worker.run()
    assert "captured:gadget:Smart Paste result" in events
    assert "finished" in events
    assert not any(e.startswith("error") for e in events)


class SpyOrchestrator(MockOrchestrator):
    def __init__(self, payload=None, error=None):
        super().__init__(payload=payload, error=error)
        self.calls = 0

    def capture(self):
        self.calls += 1
        return super().capture()


def test_lookup_worker_prefers_recent_copy_over_stale_selection(qapp, monkeypatch):
    """Lookup from the palette must use the palette copy, not the source app's
    leftover selection (SCOPE-30 QA)."""
    monkeypatch.setattr("contextual_intelligence.ui.worker.read_text_clipboard", lambda: "gadget")
    # Capture would succeed with the stale source-app selection
    orch = SpyOrchestrator(
        payload=ContextPayload(selected_text="stale old selection", tier=CaptureTier.UIA)
    )
    recent = RecentAppCopy(text="gadget", copied_at=time.monotonic(), source="smart_paste")
    worker = LookupWorker(
        orch, MockLlmClient(), recent_copy=recent, prefer_recent_copy=True
    )

    captured = []
    worker.capture_succeeded.connect(lambda p: captured.append(p.selected_text))
    worker.run()
    assert captured == ["gadget"]
    assert orch.calls == 0  # capture never ran; palette copy took priority


def test_lookup_worker_prefer_flag_falls_back_to_capture(qapp, monkeypatch):
    """With no valid palette copy, the prefer flag must not bypass selection-first capture."""
    monkeypatch.setattr(
        "contextual_intelligence.ui.worker.read_text_clipboard", lambda: "something else"
    )
    orch = SpyOrchestrator()  # captures "word"
    recent = RecentAppCopy(text="gadget", copied_at=time.monotonic(), source="smart_paste")
    worker = LookupWorker(
        orch, MockLlmClient(), recent_copy=recent, prefer_recent_copy=True
    )

    captured = []
    worker.capture_succeeded.connect(lambda p: captured.append(p.selected_text))
    worker.run()
    assert captured == ["word"]
    assert orch.calls == 1


def test_lookup_worker_invalid_recent_copy_validation_is_sanitized(qapp, monkeypatch, caplog):
    secret = "PRIVATE_RECENT_COPY_DETAIL"
    invalid_text = f"{secret}�"
    monkeypatch.setattr("contextual_intelligence.ui.worker.read_text_clipboard", lambda: invalid_text)
    orch = SpyOrchestrator()  # captures "word" after rejecting the invalid handoff
    recent = RecentAppCopy(text=invalid_text, copied_at=time.monotonic(), source="smart_paste")
    worker = LookupWorker(
        orch, MockLlmClient(), recent_copy=recent, prefer_recent_copy=True
    )

    captured = []
    worker.capture_succeeded.connect(lambda p: captured.append(p.selected_text))
    worker.run()

    assert captured == ["word"]
    assert secret not in caplog.text


def test_lookup_worker_recent_copy_stale_rejected(qapp, monkeypatch):
    monkeypatch.setattr("contextual_intelligence.ui.worker.read_text_clipboard", lambda: "gadget")
    orch = MockOrchestrator(error=CaptureError("no selection", CaptureTier.UIA))
    recent = RecentAppCopy(
        text="gadget", copied_at=time.monotonic() - 120.0, source="smart_paste"
    )
    worker = LookupWorker(orch, MockLlmClient(), recent_copy=recent)

    captured, errors = [], []
    worker.capture_succeeded.connect(captured.append)
    worker.error_occurred.connect(errors.append)
    worker.run()
    assert not captured
    assert len(errors) == 1


def test_lookup_worker_recent_copy_mismatch_rejected(qapp, monkeypatch):
    """If the clipboard has changed since the in-app copy, the handoff must not fire."""
    monkeypatch.setattr(
        "contextual_intelligence.ui.worker.read_text_clipboard", lambda: "something else entirely"
    )
    orch = MockOrchestrator(error=CaptureError("no selection", CaptureTier.UIA))
    recent = RecentAppCopy(text="gadget", copied_at=time.monotonic(), source="smart_paste")
    worker = LookupWorker(orch, MockLlmClient(), recent_copy=recent)

    captured, errors = [], []
    worker.capture_succeeded.connect(captured.append)
    worker.error_occurred.connect(errors.append)
    worker.run()
    assert not captured
    assert len(errors) == 1


def test_lookup_worker_cancellation(qapp):
    orch = MockOrchestrator()
    llm = MockLlmClient(tokens=["1", "2", "3", "4", "5"])
    worker = LookupWorker(orch, llm)

    tokens = []
    worker.token_received.connect(lambda t: tokens.append(t))
    # Cancel after receiving the second token
    worker.token_received.connect(lambda t: worker.cancel() if len(tokens) == 2 else None)

    worker.run()
    assert len(tokens) == 2


def test_popup_rapid_double_trigger(qapp):
    popup = LookupPopupWindow()
    orch = MockOrchestrator()
    llm = MockLlmClient()

    worker1 = LookupWorker(orch, llm)
    worker2 = LookupWorker(orch, llm)

    # Start first lookup
    popup.start_lookup(worker1)
    assert popup._worker is worker1
    assert worker1.isRunning()

    # Start second lookup rapidly
    popup.start_lookup(worker2)
    # The second trigger should be ignored since worker1 is still running
    assert popup._worker is worker1

    # Clean up
    worker1.cancel()
    worker1.wait()


def test_lookup_worker_skips_llm_on_oversized_selection(qapp):
    class SpyLlmClient(MockLlmClient):
        def __init__(self):
            super().__init__()
            self.called = False

        def stream_lookup(self, payload):
            self.called = True
            yield from super().stream_lookup(payload)

    orch = MockOrchestrator(payload=ContextPayload(selected_text="x" * 200, tier=CaptureTier.UIA, app_name="test"))
    llm = SpyLlmClient()
    worker = LookupWorker(orch, llm)

    finished = []
    worker.finished_lookup.connect(lambda: finished.append(True))
    worker.run()

    assert not llm.called
    assert len(finished) == 1


def test_lookup_worker_unexpected_capture_error_is_sanitized(qapp, caplog):
    secret = "PRIVATE_CAPTURE_DETAIL"
    orch = MockOrchestrator(error=RuntimeError(secret))
    worker = LookupWorker(orch, MockLlmClient())

    errors = []
    worker.error_occurred.connect(errors.append)
    worker.run()

    assert errors == ["Lookup failed during capture. Try selecting the text again or restarting the app."]
    assert secret not in errors[0]
    assert secret not in caplog.text


def test_lookup_worker_llm_error_is_sanitized(qapp, caplog):
    secret = "PRIVATE_LLM_DETAIL"
    worker = LookupWorker(MockOrchestrator(), MockLlmClient(error=RuntimeError(secret)))

    errors = []
    worker.error_occurred.connect(errors.append)
    worker.run()

    assert errors == ["The local model returned an error. Check LM Studio and try again."]
    assert secret not in errors[0]
    assert secret not in caplog.text
