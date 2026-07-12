"""Typed capture payloads.

Every extraction result is wrapped in a ContextPayload before it reaches the
model. Validation rejects low-quality captures (empty, mojibake, oversized
selections) here rather than sending them downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from contextual_intelligence.paste_presets import PastePresetId, get_paste_preset

# A "selection" is a word, short phrase, or sentence/passage.
MAX_SELECTION_CHARS = 1000
MAX_LOOKUP_CHARS = 150
MAX_PASTE_INPUT_CHARS = 8000
# Control characters other than tab/newline signal a broken capture.
MAX_CONTROL_CHAR_RATIO = 0.05


class CaptureTier(StrEnum):
    UIA = "uia"
    CLIPBOARD = "clipboard"
    OCR = "ocr"


# A copy the app itself placed on the clipboard is only trusted as a lookup
# handoff while fresh — beyond this window the clipboard is treated as
# arbitrary user state again (SCOPE-30).
RECENT_COPY_TTL_SECONDS = 60.0


@dataclass
class RecentAppCopy:
    """Short text recently copied from inside the app (e.g. Smart Paste result).

    Not a capture tier: consulted only after all capture providers fail, and
    only when the clipboard still holds exactly this text.
    """

    text: str
    copied_at: float  # time.monotonic()
    source: Literal["smart_paste"]


class SnapshotStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    EMPTY = "empty"
    TEXT = "text"
    UNSUPPORTED = "unsupported"


class RestoreOutcome(StrEnum):
    NO_OWNERSHIP = "no_ownership"
    EXTERNAL_CHANGE = "external_change"
    RESTORED = "restored"
    FAILED = "failed"
    FAILED_CLEARED = "failed_cleared"


class RestoreFailureFlavor(StrEnum):
    NEVER_WROTE = "never_wrote"
    CLEARED = "cleared"


class CaptureError(Exception):
    """A capture attempt failed. `reason` is logged as telemetry."""

    def __init__(self, reason: str, tier: CaptureTier | None = None):
        self.reason = reason
        self.tier = tier
        self.is_terminal = False
        super().__init__(reason if tier is None else f"[{tier}] {reason}")


class ProtectedFieldError(CaptureError):
    def __init__(self, reason: str, tier: CaptureTier | None = None):
        super().__init__(reason, tier)
        self.is_terminal = True


class CaptureIntegrityError(CaptureError):
    def __init__(self, reason: str, flavor: RestoreFailureFlavor, tier: CaptureTier | None = None):
        self.flavor = flavor
        super().__init__(reason, tier)
        self.is_terminal = True


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
            # Graceful degradation (SCOPE-26): instead of raising ValueError and failing
            # capture tiers when user selects a long passage or sentence, truncate cleanly.
            value = (value[: MAX_SELECTION_CHARS - 3].rsplit(" ", 1)[0] or value[: MAX_SELECTION_CHARS - 3]) + "..."
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


class PastePayload(BaseModel):
    text: str
    instruction: str = ""
    preset_id: PastePresetId = PastePresetId.PLAIN
    app_name: str = ""
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("text", "instruction", mode="before")
    @classmethod
    def _normalize(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("fields must be strings")
        return value.replace("\r\n", "\n").replace("\r", "\n")

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty clipboard text")
        if len(value) > MAX_PASTE_INPUT_CHARS:
            raise ValueError(
                f"clipboard text too long ({len(value)} chars > {MAX_PASTE_INPUT_CHARS}); "
                "not a smart paste target"
            )
        return value

    @field_validator("instruction")
    @classmethod
    def _validate_instruction(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _reject_mojibake(self) -> PastePayload:
        if _looks_like_mojibake(self.text):
            raise ValueError("clipboard text looks like mojibake or binary noise")
        if _looks_like_mojibake(self.instruction):
            raise ValueError("instruction looks like mojibake or binary noise")
        if not self.instruction and not get_paste_preset(self.preset_id).allows_format_only:
            raise ValueError("empty instruction: instruction required for Plain preset")
        return self


class PasteResult(BaseModel):
    payload: PastePayload
    transformed_text: str
    duration_ms: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("transformed_text", mode="before")
    @classmethod
    def _normalize(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("transformed_text must be a string")
        return value.replace("\r\n", "\n").replace("\r", "\n")
