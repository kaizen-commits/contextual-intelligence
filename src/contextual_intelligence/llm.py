"""LM Studio client (OpenAI-compatible) and lookup prompt construction."""

from __future__ import annotations

import logging
from typing import Iterator

from openai import OpenAI

from contextual_intelligence.config import Settings
from contextual_intelligence.models import ContextPayload

log = logging.getLogger(__name__)

# Card shape recovered from the original app's screen recording:
# title `term (part of speech)`, one concise definition, a contextual
# domain line, a synonyms line. Non-chatty by design.
SYSTEM_PROMPT = """\
You are a contextual dictionary. The user gives you a selected term and the \
passage it appeared in. Reply with exactly four lines shaped like this example \
and nothing else:

latticework (noun)
A structure of crossed strips; figuratively, an interconnected framework of ideas.
Context: [Investing] Munger's metaphor for combining models from many disciplines.
Synonyms: framework, lattice, grid, mesh

Line 1 is the user's term plus its part of speech in parentheses. Line 2 defines \
the term as used in the passage, one or two sentences. Line 3 names the passage's \
domain in brackets and what the term means there. Line 4 lists 3-5 synonyms, or "none"."""


def build_lookup_prompt(payload: ContextPayload, max_context_chars: int) -> str:
    before, after = payload.context_window(max_context_chars)
    if payload.has_context:
        passage = f"{before}[[{payload.selected_text}]]{after}"
        return (
            f"Term: {payload.selected_text}\n\n"
            f"Passage (term marked with [[...]]):\n{passage}"
        )
    return (
        f"Term: {payload.selected_text}\n\n"
        f"No surrounding passage was captured (source app: "
        f"{payload.app_name or 'unknown'}). Give the general meaning."
    )


class LlmClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.request_timeout_s,
        )

    def list_models(self) -> list[str]:
        return [m.id for m in self._client.models.list()]

    def stream_lookup(self, payload: ContextPayload) -> Iterator[str]:
        prompt = build_lookup_prompt(payload, self._settings.max_prompt_context_chars)
        log.debug("lookup prompt (%d chars)", len(prompt))
        stream = self._client.chat.completions.create(
            model=self._settings.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self._settings.max_answer_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    def smoke(self) -> str:
        """One tiny non-streamed round trip; returns the model's reply."""
        response = self._client.chat.completions.create(
            model=self._settings.model,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=10,
        )
        return (response.choices[0].message.content or "").strip()
