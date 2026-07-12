"""Built-in Smart Paste transformation presets.

Presets supply output contracts to the local model. Only presets whose contract
fully defines a transformation may be submitted without an extra instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PastePresetId(StrEnum):
    PLAIN = "plain"
    MARKDOWN = "markdown"
    MARKDOWN_TABLE = "markdown_table"
    JSON = "json"
    ACTION_ITEMS = "action_items"


@dataclass(frozen=True)
class PastePreset:
    id: PastePresetId
    label: str
    output_contract: str
    allows_format_only: bool = False


BUILT_IN_PASTE_PRESETS: tuple[PastePreset, ...] = (
    PastePreset(
        id=PastePresetId.PLAIN,
        label="Plain",
        output_contract=(
            "Follow the user's instruction while preserving the source meaning. "
            "Return plain text unless the instruction explicitly requests another format."
        ),
    ),
    PastePreset(
        id=PastePresetId.MARKDOWN,
        label="Markdown",
        output_contract=(
            "Transform the input into clear, well-structured GitHub-flavored Markdown. "
            "Preserve source meaning and do not wrap the result in a code fence."
        ),
        allows_format_only=True,
    ),
    PastePreset(
        id=PastePresetId.MARKDOWN_TABLE,
        label="Markdown table",
        output_contract=(
            "Convert the input into a valid GitHub-flavored Markdown table. "
            "Infer useful column headings conservatively, preserve source meaning, "
            "and do not add commentary or a code fence."
        ),
        allows_format_only=True,
    ),
    PastePreset(
        id=PastePresetId.JSON,
        label="JSON",
        output_contract=(
            "Convert the input into valid JSON that preserves the source meaning and "
            "structure. Return JSON only, with no commentary or code fence."
        ),
        allows_format_only=True,
    ),
    PastePreset(
        id=PastePresetId.ACTION_ITEMS,
        label="Action items",
        output_contract=(
            "Extract concrete action items from the input as a concise Markdown task list. "
            "Preserve named owners and deadlines when present; do not invent missing details "
            "or add commentary."
        ),
        allows_format_only=True,
    ),
)

_PRESETS_BY_ID = {preset.id: preset for preset in BUILT_IN_PASTE_PRESETS}


def get_paste_preset(preset_id: PastePresetId) -> PastePreset:
    return _PRESETS_BY_ID[preset_id]
