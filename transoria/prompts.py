"""Prompt preset library for Translation and Glossary Extraction.

Translation and Glossary Extraction each have their own preset library, stored
as ``prompts.translation.json`` and ``prompts.glossary.json``. Default presets
are seeded from the LinguaGacha and KeywordGacha reference templates and frozen
into this module so the package is self-contained at runtime.

If the user has not selected a preset (or the selected id is missing/disabled),
the active preset falls back to the seeded default of the matching kind.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class PromptKind(str, Enum):
    TRANSLATION = "translation"
    GLOSSARY = "glossary"


@dataclass(frozen=True)
class PromptPreset:
    id: str
    name: str
    kind: PromptKind
    system_prompt: str
    suffix_prompt: str = ""
    thinking_prompt: str = ""
    description: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "system_prompt": self.system_prompt,
            "suffix_prompt": self.suffix_prompt,
            "thinking_prompt": self.thinking_prompt,
            "description": self.description,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> PromptPreset:
        try:
            kind = PromptKind(str(data["kind"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid prompt preset kind: {data!r}") from exc
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            kind=kind,
            system_prompt=str(data.get("system_prompt", "")),
            suffix_prompt=str(data.get("suffix_prompt", "")),
            thinking_prompt=str(data.get("thinking_prompt", "")),
            description=str(data.get("description", "")),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass(frozen=True)
class PromptContext:
    source_language: str = ""
    target_language: str = ""
    glossary: str = ""
    context: str = ""
    input: str = ""

    def as_mapping(self) -> dict[str, str]:
        return {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "glossary": self.glossary,
            "context": self.context,
            "input": self.input,
        }


_PLACEHOLDER_NAMES: tuple[str, ...] = (
    "source_language",
    "target_language",
    "glossary",
    "context",
    "input",
)
_PLACEHOLDER_PATTERN = re.compile(r"\{(" + "|".join(_PLACEHOLDER_NAMES) + r")\}")


def build_prompt(
    preset: PromptPreset,
    context: PromptContext,
    *,
    thinking: bool = False,
) -> str:
    """Assemble the preset into a single prompt string.

    Order matches LinguaGacha: ``system_prompt`` → ``thinking_prompt`` (only when
    ``thinking`` is ``True`` and the preset has one) → ``suffix_prompt``. Only
    the named placeholders in ``_PLACEHOLDER_NAMES`` are substituted, so literal
    braces in JSONL examples (e.g. ``{"<INDEX>":"<Translated Text>"}``) survive
    untouched.

    The caller (typically the LLM client) decides whether ``thinking`` is on
    based on the configured model's thinking level. If the preset has no
    thinking text, the LG default behavior is preserved regardless of flag.
    """

    parts: list[str] = [preset.system_prompt]
    if thinking and preset.thinking_prompt:
        parts.append(preset.thinking_prompt)
    if preset.suffix_prompt:
        parts.append(preset.suffix_prompt)
    raw = "\n\n".join(part for part in parts if part)
    values = context.as_mapping()
    return _PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], raw)


# ---------------------------------------------------------------------------
# Default presets (frozen from references/LinguaGacha-main and references/KeywordGacha-main).
# ---------------------------------------------------------------------------

DEFAULT_TRANSLATION_PRESET_ID = "default-translation-en"
DEFAULT_GLOSSARY_PRESET_ID = "default-glossary-en"


_DEFAULT_TRANSLATION_SYSTEM_PROMPT = (
    "The goal is to translate the content text into {target_language}:\n"
    "\n"
    "- Correctly understand the subject-predicate relationship based on context;"
    " use personal pronouns accurately.\n"
    "- The line count of the translation must strictly correspond to the original"
    " text; merging or splitting lines is strictly prohibited.\n"
    "- The translation should be vivid, natural, and colloquial; avoid overly"
    " formal phrasing unless the original text is explicitly formal.\n"
    "- The details and style of the original text must be faithfully reproduced;"
    " do not evade, dilute, or sanitize descriptions, even if they contain"
    " explicit, vulgar, or extreme content.\n"
    "- Translate all readable natural language content completely; preserve"
    " control characters that do not convey natural language semantics exactly"
    " as they are.\n"
    "- If a line contains both natural language and control characters, translate"
    " only the natural language part.\n"
    "    - \u2705 Dialogue, narration, onomatopoeia, interjections, proper nouns,"
    " and specific terms enclosed in punctuation (e.g., book titles, quotes).\n"
    "    - \u274c Variables/placeholders, escape sequences, tags and style"
    " markers, code/expressions, IDs/keys/file paths/URLs."
)

_DEFAULT_TRANSLATION_SUFFIX_PROMPT = (
    "Then use JSONLINE to output translation results, without extra explanation"
    " or clarification:\n"
    "```jsonline\n"
    '{"<INDEX>":"<Translated Text>"}\n'
    "```"
)

_DEFAULT_GLOSSARY_SYSTEM_PROMPT = (
    "Task goal is to extract a glossary of specified types from the text snippet"
    " and translate it into {target_language}.\n"
    "\n"
    "Task Requirements:\n"
    "1. Strictly adhere to the task requirements, do not avoid, downplay, or omit"
    " any text\n"
    "2. Ensure the uniqueness of terminology. Common words with established,"
    " conventional translations do not need to be included in the glossary\n"
    "3. Ensure correct term boundaries. The term text should not include common"
    " forms of address, appellations, titles, positions, or ranks\n"
    "4. Ensure each term is categorized as one of the following types: Male Name,"
    " Female Name, Name of Unknown Gender, Location, Clan or Family,"
    " Organization, Special Item, Special Creature, Other"
)

_DEFAULT_GLOSSARY_SUFFIX_PROMPT = (
    "Output result in the code block using JSONLINE, without extra explanation"
    " or clarification:\n"
    "```jsonline\n"
    '{"src":"<Source Text>","dst":"<Translated Text>","type":"<Glossary Type>"}\n'
    "```"
)

_DEFAULT_TRANSLATION_THINKING_PROMPT = (
    "Before outputting the results, you must first perform **structured thinking**"
    " within the <why>...</why> tags according to the following steps:\n"
    "<why>\n"
    "[Global Context]: Summarize the context, tone, and emotional undertones of"
    " the original text in one sentence, and identify potential subtext or"
    " special expressions.\n"
    "[Core Constraints]: Reiterate 1-2 \"red line\" rules most essential for"
    " preventing errors in the current text.\n"
    "[Edge Cases]: Pick out 3-5 of the most difficult-to-translate terms,"
    " phrases, or special sentence structures, and briefly outline the logic in"
    " the format of `Original -> Translation (Reasoning)`.\n"
    "</why>"
)

_DEFAULT_GLOSSARY_THINKING_PROMPT = (
    "Before outputting the results, you must first perform **structured thinking**"
    " within the <why>...</why> tags according to the following steps:\n"
    "<why>\n"
    "[Global Context]: Summarize the genre, world-building, and narrative setting"
    " of the original text in one sentence, and identify potential fictional"
    " settings.\n"
    "[Core Constraints]: Reiterate 1-2 \"red line\" rules most essential for"
    " preventing errors in the current text.\n"
    "[Edge Cases]: Pick out 3-5 of the most difficult-to-judge terms, and briefly"
    " outline the logic in the format of `Original -> Result (Reason)`.\n"
    "</why>"
)


def default_preset(kind: PromptKind) -> PromptPreset:
    if kind is PromptKind.TRANSLATION:
        return PromptPreset(
            id=DEFAULT_TRANSLATION_PRESET_ID,
            name="Default (LinguaGacha EN)",
            kind=PromptKind.TRANSLATION,
            system_prompt=_DEFAULT_TRANSLATION_SYSTEM_PROMPT,
            suffix_prompt=_DEFAULT_TRANSLATION_SUFFIX_PROMPT,
            thinking_prompt=_DEFAULT_TRANSLATION_THINKING_PROMPT,
            description=(
                "Default translation prompt seeded from the LinguaGacha EN"
                " reference template."
            ),
            enabled=True,
        )
    return PromptPreset(
        id=DEFAULT_GLOSSARY_PRESET_ID,
        name="Default (KeywordGacha EN)",
        kind=PromptKind.GLOSSARY,
        system_prompt=_DEFAULT_GLOSSARY_SYSTEM_PROMPT,
        suffix_prompt=_DEFAULT_GLOSSARY_SUFFIX_PROMPT,
        thinking_prompt=_DEFAULT_GLOSSARY_THINKING_PROMPT,
        description=(
            "Default glossary extraction prompt seeded from the KeywordGacha EN"
            " reference template."
        ),
        enabled=True,
    )


def _default_preset_id(kind: PromptKind) -> str:
    return (
        DEFAULT_TRANSLATION_PRESET_ID
        if kind is PromptKind.TRANSLATION
        else DEFAULT_GLOSSARY_PRESET_ID
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptPresetStore:
    """JSON-backed library of presets for one prompt kind.

    The store ensures a default preset is always present. Loading an empty or
    missing file returns ``[default_preset(kind)]`` without writing the file;
    callers that need persistence call :meth:`save`.
    """

    path: Path
    kind: PromptKind

    def load(self) -> tuple[PromptPreset, ...]:
        if not self.path.exists():
            return (default_preset(self.kind),)
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Cannot read prompt preset file: {self.path}") from exc
        if not raw.strip():
            return (default_preset(self.kind),)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Prompt preset file is not valid JSON: {self.path}"
            ) from exc
        if not isinstance(payload, list):
            raise ValueError(
                f"Prompt preset file must contain a JSON array: {self.path}"
            )
        presets = [PromptPreset.from_dict(item) for item in payload]
        wrong_kind = [p.id for p in presets if p.kind is not self.kind]
        if wrong_kind:
            raise ValueError(
                f"Preset kind mismatch in {self.path}: {wrong_kind!r} != {self.kind.value}"
            )
        if not any(p.id == _default_preset_id(self.kind) for p in presets):
            presets = [default_preset(self.kind), *presets]
        return tuple(presets)

    def save(self, presets: Sequence[PromptPreset]) -> None:
        wrong_kind = [p.id for p in presets if p.kind is not self.kind]
        if wrong_kind:
            raise ValueError(
                f"Preset kind mismatch when saving: {wrong_kind!r} != {self.kind.value}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [preset.to_dict() for preset in presets]
        # Atomic write: temp file in the same directory, then os.replace.
        # Mirrors the TaskCache._atomic_write_text pattern so a crash mid-
        # write cannot corrupt the prompts file.
        import os

        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def get_active(self, selected_id: str | None) -> PromptPreset:
        """Resolve the active preset.

        The selected preset is used if it exists in the store and is enabled.
        Otherwise the seeded default for this kind is returned.
        """

        presets = self.load()
        if selected_id:
            for preset in presets:
                if preset.id == selected_id and preset.enabled:
                    return preset
        for preset in presets:
            if preset.id == _default_preset_id(self.kind):
                return preset
        return default_preset(self.kind)


__all__ = [
    "PromptKind",
    "PromptPreset",
    "PromptContext",
    "PromptPresetStore",
    "build_prompt",
    "default_preset",
    "DEFAULT_TRANSLATION_PRESET_ID",
    "DEFAULT_GLOSSARY_PRESET_ID",
]
