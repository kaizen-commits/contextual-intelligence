import pytest
from pydantic import ValidationError

from contextual_intelligence.capture import CaptureOrchestrator
from contextual_intelligence.models import CaptureError, CaptureTier, ContextPayload


def payload(tier=CaptureTier.UIA) -> ContextPayload:
    return ContextPayload(selected_text="word", tier=tier)


class Succeeds:
    name = "ok"

    def capture(self):
        return payload()


class Fails:
    name = "broken"

    def capture(self):
        raise CaptureError("nope", CaptureTier.UIA)


def test_requires_providers():
    with pytest.raises(ValueError):
        CaptureOrchestrator([])


def test_first_success_wins():
    calls = []

    class Recorder:
        def __init__(self, name):
            self.name = name

        def capture(self):
            calls.append(self.name)
            return payload()

    orch = CaptureOrchestrator([Recorder("first"), Recorder("second")])
    orch.capture()
    assert calls == ["first"]


def test_falls_through_to_next_tier():
    orch = CaptureOrchestrator([Fails(), Succeeds()])
    result = orch.capture()
    assert result.selected_text == "word"
    assert [a.ok for a in orch.last_attempts] == [False, True]
    assert orch.last_attempts[0].error == "nope"


def test_all_tiers_failing_raises_with_reasons():
    orch = CaptureOrchestrator([Fails(), Fails()])
    with pytest.raises(CaptureError, match="all capture tiers failed.*broken: nope"):
        orch.capture()
    assert len(orch.last_attempts) == 2


def test_attempts_reset_between_captures():
    orch = CaptureOrchestrator([Succeeds()])
    orch.capture()
    orch.capture()
    assert len(orch.last_attempts) == 1


def test_catches_validation_error_and_falls_through():
    class Invalid:
        name = "invalid"

        def capture(self):
            return ContextPayload(selected_text="", tier=CaptureTier.UIA)

    orch = CaptureOrchestrator([Invalid(), Succeeds()])
    result = orch.capture()
    assert result.selected_text == "word"
    assert [a.ok for a in orch.last_attempts] == [False, True]
    assert orch.last_attempts[0].error == "validation error"


def test_validation_error_reason_does_not_include_capture_content(caplog):
    secret = "PRIVATE_SELECTED_TEXT"

    class InvalidSensitive:
        name = "invalid"

        def capture(self):
            try:
                ContextPayload(selected_text=secret, before="�", tier=CaptureTier.UIA)
            except ValidationError as exc:
                raise exc
            raise AssertionError("expected validation error")

    orch = CaptureOrchestrator([InvalidSensitive()])
    with pytest.raises(CaptureError):
        orch.capture()

    assert len(orch.last_attempts) == 1
    assert orch.last_attempts[0].error == "validation error"
    assert secret not in caplog.text
    assert secret not in str(orch.last_attempts[0].error)
