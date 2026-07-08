import pytest
from pydantic import ValidationError

from contextual_intelligence.models import (
    MAX_PASTE_INPUT_CHARS,
    MAX_SELECTION_CHARS,
    CaptureTier,
    ContextPayload,
    PastePayload,
    PasteResult,
)


def make(**overrides) -> ContextPayload:
    kwargs = dict(
        selected_text="mental models",
        before="Charlie Munger championed the idea of ",
        after=" as a latticework for decision making.",
        app_name="chrome.exe",
        window_title="Article — Chrome",
        tier=CaptureTier.UIA,
    )
    kwargs.update(overrides)
    return ContextPayload(**kwargs)


def test_valid_payload():
    p = make()
    assert p.selected_text == "mental models"
    assert p.has_context
    assert p.confidence == 1.0


def test_selection_is_stripped():
    assert make(selected_text="  word  ").selected_text == "word"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_empty_selection_rejected(bad):
    with pytest.raises(ValidationError, match="empty selection"):
        make(selected_text=bad)


def test_oversized_selection_degrades_gracefully():
    p = make(selected_text="word " * 300)
    assert len(p.selected_text) <= MAX_SELECTION_CHARS
    assert p.selected_text.endswith("...")


def test_symbol_only_selection_rejected():
    with pytest.raises(ValidationError, match="no word characters"):
        make(selected_text="--- ***")


def test_mojibake_rejected():
    with pytest.raises(ValidationError, match="mojibake"):
        make(selected_text="mo�els")


def test_control_noise_in_context_rejected():
    with pytest.raises(ValidationError, match="mojibake"):
        make(before="\x00\x01\x02\x03garbage")


def test_crlf_normalized():
    p = make(before="line one\r\nline two ")
    assert "\r" not in p.before


def test_no_context_payload_is_valid():
    p = make(before="", after="")
    assert not p.has_context


def test_context_window_centres_selection():
    p = make(before="a" * 1000, after="b" * 1000)
    before, after = p.context_window(200)
    assert len(before) == 100 and len(after) == 100
    assert before == "a" * 100 and after == "b" * 100


def test_context_window_reallocates_unused_budget():
    p = make(before="a" * 1000, after="b" * 10)
    before, after = p.context_window(200)
    assert after == "b" * 10
    assert len(before) == 190  # gets the budget `after` didn't use


def test_context_window_short_context_untouched():
    p = make()
    assert p.context_window(1500) == (p.before, p.after)


def test_paste_payload_valid():
    p = PastePayload(text="hello world", instruction="summarize", app_name="notepad.exe")
    assert p.text == "hello world"
    assert p.instruction == "summarize"
    assert p.app_name == "notepad.exe"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_paste_payload_empty_text_rejected(bad):
    with pytest.raises(ValidationError, match="empty clipboard text"):
        PastePayload(text=bad, instruction="do something")


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_paste_payload_empty_instruction_rejected(bad):
    with pytest.raises(ValidationError, match="empty instruction"):
        PastePayload(text="some text", instruction=bad)


def test_paste_payload_oversized_text_rejected():
    with pytest.raises(ValidationError, match="too long"):
        PastePayload(text="x" * (MAX_PASTE_INPUT_CHARS + 1), instruction="do something")


def test_paste_payload_mojibake_rejected():
    with pytest.raises(ValidationError, match="mojibake"):
        PastePayload(text="mo\ufffdels", instruction="fix")


def test_paste_result_valid():
    p = PastePayload(text="hello", instruction="upper")
    res = PasteResult(payload=p, transformed_text="HELLO", duration_ms=150.0)
    assert res.transformed_text == "HELLO"
    assert res.duration_ms == 150.0
