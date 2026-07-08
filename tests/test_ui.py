import pytest
from PySide6.QtWidgets import QApplication

from contextual_intelligence.models import CaptureError, CaptureTier, ContextPayload
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


def test_lookup_worker_capture_error(qapp):
    orch = MockOrchestrator(error=CaptureError("no selection", CaptureTier.UIA))
    llm = MockLlmClient()
    worker = LookupWorker(orch, llm)

    errors = []
    worker.error_occurred.connect(lambda msg: errors.append(msg))
    worker.run()
    assert len(errors) == 1
    assert "no selection" in errors[0]


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
