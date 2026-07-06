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


def test_popup_window_error_state(qapp):
    popup = LookupPopupWindow()
    popup._on_error("Capture failed: empty selection")
    assert "❌ Capture failed: empty selection" in popup.status_label.text()
    assert popup.title_label.isHidden()


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
