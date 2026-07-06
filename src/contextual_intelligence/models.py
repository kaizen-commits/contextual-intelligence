"""Typed capture payloads.

Every extraction result is wrapped in a ContextPayload before it reaches the
model. Validation rejects low-quality captures (empty, mojibake, oversized
selections) here rather than sending them downstream.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

# A "selection" is a word or short phrase, not a document.
MAX_SELECTION_CHARS = 400
# Control characters other than tab/newline signal a broken capture.
MAX_CONTROL_CHAR_RATIO = 0.05


class CaptureTier(StrEnum):
    UIA = "uia"
    CLIPBOARD = "clipboard"
    OCR = "ocr"


class CaptureError(Exception):
    """A capture attempt failed. `reason` is logged as telemetry."""

    def __init__(self, reason: str, tier: CaptureTier | None = None):
        self.reason = reason
        self.tier = tier
        super().__init__(reason if tier is None else f"[{tier}] {reason}")


def _looks_like_mojibake(text: str) -> bool:
    if "�" in text:
        return True
    if not text:
        return False
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n")
    return control / len(text) > MAX_CONTROL_CHAR_RATIO


class ContextPayload(BaseModel):
    selected_text: str
    before: str = ""
    after: str = ""
    app_name: str = ""
    window_title: str = ""
    tier: CaptureTier
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("selected_text", "before", "after", mode="before")
    @classmethod
    def _normalize(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("text fields must be strings")
        return value.replace("\r\n", "\n").replace("\r", "\n")

    @field_validator("selected_text")
    @classmethod
    def _validate_selection(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty selection")
        if len(value) > MAX_SELECTION_CHARS:
            raise ValueError(
                f"selection too long ({len(value)} chars > {MAX_SELECTION_CHARS}); "
                "not a lookup target"
            )
        if not any(ch.isalnum() for ch in value):
            raise ValueError("selection contains no word characters")
        return value

    @model_validator(mode="after")
    def _reject_mojibake(self) -> ContextPayload:
        for name in ("selected_text", "before", "after"):
            if _looks_like_mojibake(getattr(self, name)):
                raise ValueError(f"{name} looks like mojibake or binary noise")
        return self

    @property
    def has_context(self) -> bool:
        return bool(self.before.strip() or self.after.strip())

    def context_window(self, max_chars: int) -> tuple[str, str]:
        """Truncate surrounding context to max_chars total, keeping the
        selection centred: truncate `before` from its start and `after`
        from its end."""
        half = max_chars // 2
        before, after = self.before, self.after
        # Give unused budget from one side to the other.
        budget_before = half + max(0, half - len(after))
        budget_after = half + max(0, half - len(before))
        if len(before) > budget_before:
            before = before[-budget_before:]
        if len(after) > budget_after:
            after = after[:budget_after]
        return before, after
