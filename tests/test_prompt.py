from contextual_intelligence.llm import (
    PASTE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_lookup_prompt,
    build_paste_prompt,
)
from contextual_intelligence.models import CaptureTier, ContextPayload, PastePayload


def test_prompt_marks_selection_in_passage():
    p = ContextPayload(
        selected_text="latticework",
        before="a ",
        after=" of models",
        tier=CaptureTier.UIA,
    )
    prompt = build_lookup_prompt(p, max_context_chars=1500)
    assert "Term: latticework" in prompt
    assert "a [[latticework]] of models" in prompt


def test_prompt_truncates_context_to_cap():
    p = ContextPayload(
        selected_text="word",
        before="x" * 5000,
        after="y" * 5000,
        tier=CaptureTier.UIA,
    )
    prompt = build_lookup_prompt(p, max_context_chars=1000)
    passage = prompt.split("Passage", 1)[1]
    assert len(passage) < 1200  # cap + selection + markers, not 10k


def test_prompt_without_context_asks_general_meaning():
    p = ContextPayload(selected_text="word", tier=CaptureTier.CLIPBOARD, app_name="app.exe")
    prompt = build_lookup_prompt(p, max_context_chars=1500)
    assert "No surrounding passage" in prompt
    assert "app.exe" in prompt


def test_system_prompt_pins_card_shape():
    for marker in ("part of speech", "Context:", "Synonyms:"):
        assert marker in SYSTEM_PROMPT


def test_build_paste_prompt_with_app():
    p = PastePayload(text="some code", instruction="explain", app_name="vscode.exe")
    prompt = build_paste_prompt(p)
    assert "Instruction: explain" in prompt
    assert "(from vscode.exe)" in prompt
    assert "some code" in prompt


def test_build_paste_prompt_without_app():
    p = PastePayload(text="some text", instruction="summarize")
    prompt = build_paste_prompt(p)
    assert "Instruction: summarize" in prompt
    assert "(from" not in prompt
    assert "some text" in prompt


def test_paste_system_prompt_instructions():
    assert "ONLY the transformed text directly" in PASTE_SYSTEM_PROMPT
