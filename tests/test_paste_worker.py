import pytest
from PySide6.QtWidgets import QApplication
from openai import APIConnectionError

from contextual_intelligence.models import PastePayload
from contextual_intelligence.ui.paste_worker import PasteWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class MockLlmClient:
    def __init__(self, tokens=None, error=None):
        self._tokens = tokens or ["BULLET 1\n", "BULLET 2"]
        self._error = error

    def stream_transform(self, payload):
        if self._error:
            raise self._error
        for t in self._tokens:
            yield t


class SequencedLlmClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def stream_transform(self, payload):
        self.calls += 1
        yield from self._responses.pop(0)


def test_paste_worker_success(qapp):
    payload = PastePayload(text="hello", instruction="upper", app_name="test.exe")
    llm = MockLlmClient(tokens=["HEL", "LO"])
    worker = PasteWorker(payload, llm)

    events = []
    worker.started_transform.connect(lambda: events.append("started"))
    worker.token_received.connect(lambda t: events.append(f"token:{t}"))
    worker.finished_transform.connect(
        lambda txt, dur: events.append(f"finished:{txt}")
    )

    worker.run()
    assert events == [
        "started",
        "token:HEL",
        "token:LO",
        "finished:HELLO",
    ]


def test_paste_worker_retries_empty_response(qapp):
    payload = PastePayload(text="hello", instruction="upper")
    llm = SequencedLlmClient([["\n", "  "], ["HELLO ", "WORLD"]])
    worker = PasteWorker(payload, llm)

    retries = []
    finished = []
    worker.retrying_transform.connect(lambda att: retries.append(att))
    worker.finished_transform.connect(lambda txt, dur: finished.append(txt))

    worker.run()
    assert llm.calls == 2
    assert retries == [2]
    assert finished == ["HELLO WORLD"]


def test_paste_worker_connection_error(qapp):
    payload = PastePayload(text="hello", instruction="upper")
    llm = MockLlmClient(error=APIConnectionError(request=None))
    worker = PasteWorker(payload, llm)

    errors = []
    worker.error_occurred.connect(lambda msg: errors.append(msg))
    worker.run()
    assert len(errors) == 1
    assert "Cannot reach LM Studio" in errors[0]


def test_paste_worker_cancellation(qapp):
    payload = PastePayload(text="hello", instruction="upper")
    llm = MockLlmClient(tokens=["1", "2", "3", "4", "5"])
    worker = PasteWorker(payload, llm)

    tokens = []
    finished = []
    worker.token_received.connect(lambda t: tokens.append(t))
    worker.token_received.connect(
        lambda t: worker.cancel() if len(tokens) == 2 else None
    )
    worker.finished_transform.connect(lambda txt, dur: finished.append(txt))

    worker.run()
    assert len(tokens) == 2
    assert len(finished) == 0
