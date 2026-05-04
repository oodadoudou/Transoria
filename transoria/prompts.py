"""Prompt preset library for Translation and Glossary Extraction.

Translation and Glossary Extraction each have their own preset library, stored
as ``prompts.translation.json`` and ``prompts.glossary.json``. The seeded
defaults are frozen into this module so the package is self-contained at
runtime.

If the user has not selected a preset (or the selected id is missing/disabled),
the active preset falls back to the primary seeded default of the matching kind.
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
    # Seeded presets (the built-in language-agnostic defaults) carry
    # ``is_system=True`` and are immutable: the bridge rejects updates
    # and deletes, and the UI shows them in view-only mode.
    is_system: bool = False

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
            "is_system": self.is_system,
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
            is_system=bool(data.get("is_system", False)),
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

    Order: ``system_prompt`` → system-level thinking guidance (only when
    ``thinking`` is ``True``) → read-only system ``suffix_prompt``.
    Only the named placeholders in ``_PLACEHOLDER_NAMES`` are substituted, so
    literal braces in JSONL examples (e.g.
    ``{"<INDEX>":"<Translated Text>"}``) survive untouched.
    """

    parts: list[str] = [preset.system_prompt]
    if thinking:
        parts.append(_system_thinking_prompt(preset.kind))
    if preset.is_system and preset.suffix_prompt:
        parts.append(preset.suffix_prompt)
    raw = "\n\n".join(part for part in parts if part)
    values = context.as_mapping()
    return _PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], raw)


def _system_thinking_prompt(kind: PromptKind) -> str:
    if kind is PromptKind.GLOSSARY:
        return (
            "Before answering, internally check candidate terms, category "
            "language, and output format. Output only the requested result."
        )
    return (
        "Before answering, internally check meaning, context, terminology, "
        "protected text, and output format. Output only the requested result."
    )


# ---------------------------------------------------------------------------
# Default presets
# ---------------------------------------------------------------------------

DEFAULT_TRANSLATION_PRESET_ID = "default-translation-zh"
DEFAULT_GLOSSARY_PRESET_ID = "default-glossary-zh"
DEFAULT_TRANSLATION_EN_ID = "default-translation-en"
DEFAULT_GLOSSARY_EN_ID = "default-glossary-en"


_TRANSLATION_SYSTEM_ZH = """\
任务：将原文翻译为 {target_language}。

要求：
- 原文与译文的行数必须严格一致，每个输入索引对应且仅对应一条译文，严禁合并行、拆分行、复用相邻索引的译文。
- 保持原文含义、信息与可见结构。
- 保留变量、占位符、转义、HTML/XML 标签、ID、URL、文件路径、代码片段等不可译内容。
- 标点忠实原文：保留原引号/括号家族，不替换。
- 遵循当前预设中的用户指令；不要添加当前预设未要求的风格、解释或评价。"""

_TRANSLATION_SYSTEM_EN = """\
Task: translate the source text into {target_language}.

Requirements:
- The line count of the translation must strictly match the source: each input index maps to exactly one translation, and merging lines, splitting lines, or reusing the translation of an adjacent index are all forbidden.
- Preserve the source meaning, information, and visible structure.
- Preserve non-translatable content verbatim, including variables, placeholders, escapes, HTML/XML tags, IDs, URLs, file paths, and code fragments.
- Preserve source punctuation: keep its quote/bracket family; do not substitute.
- Follow the active preset's user instructions; do not add style, explanation, or judgment that the active preset did not request."""

_TRANSLATION_SUFFIX_ZH = """\
然后用 JSONLINE 格式输出译文，不要任何额外解释或说明：
```jsonline
{"<INDEX>":"<译文>"}
```"""

_TRANSLATION_SUFFIX_EN = """\
Then use JSONLINE to output translation results, without extra explanation or clarification:
```jsonline
{"<INDEX>":"<Translated Text>"}
```"""

_TRANSLATION_THINKING_ZH = ""

_TRANSLATION_THINKING_EN = ""


_GLOSSARY_SYSTEM_ZH = """\
任务：从 {source_language} 文本中提取当前预设要求的术语，并译为 {target_language}。

要求：
- 遵循当前预设中关于提取范围、命名和分类的用户指令。
- 不添加当前预设未要求的筛选偏好或分类体系。
- `type` 字段必须使用 {target_language}。"""

_GLOSSARY_SYSTEM_EN = """\
Task: extract terms requested by the active preset from {source_language} text and translate them into {target_language}.

Requirements:
- Follow the active preset's user instructions for extraction scope, naming, and categorization.
- Do not add filtering preferences or a taxonomy that the active preset did not request.
- The `type` field must be written in {target_language}."""

_GLOSSARY_SUFFIX_ZH = """\
只输出 JSONLINE，每行一个独立 JSON 对象。
严禁 Markdown 表格、代码块、标题、解释、前缀、后缀。
第一个非空字符必须是 `{`。
{"src":"<原文>","dst":"<译文>","type":"<分类>"}
"""

_GLOSSARY_SUFFIX_EN = """\
Output JSONLINE only: one independent JSON object per line.
No Markdown tables, no code fences, no headings, no explanations, no prefix, no suffix.
The first non-whitespace character must be `{`.
{"src":"<Source Text>","dst":"<Translated Text>","type":"<Category>"}
"""

_GLOSSARY_THINKING_ZH = ""

_GLOSSARY_THINKING_EN = ""


def _seeded_translation_zh() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_TRANSLATION_PRESET_ID,
        name="默认",
        kind=PromptKind.TRANSLATION,
        system_prompt=_TRANSLATION_SYSTEM_ZH,
        suffix_prompt=_TRANSLATION_SUFFIX_ZH,
        thinking_prompt=_TRANSLATION_THINKING_ZH,
        description="默认翻译预设。",
        enabled=True,
        is_system=True,
    )


def _seeded_translation_en() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_TRANSLATION_EN_ID,
        name="Default",
        kind=PromptKind.TRANSLATION,
        system_prompt=_TRANSLATION_SYSTEM_EN,
        suffix_prompt=_TRANSLATION_SUFFIX_EN,
        thinking_prompt=_TRANSLATION_THINKING_EN,
        description="Default translation preset.",
        enabled=True,
        is_system=True,
    )


def _seeded_glossary_zh() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_GLOSSARY_PRESET_ID,
        name="默认",
        kind=PromptKind.GLOSSARY,
        system_prompt=_GLOSSARY_SYSTEM_ZH,
        suffix_prompt=_GLOSSARY_SUFFIX_ZH,
        thinking_prompt=_GLOSSARY_THINKING_ZH,
        description="默认术语提取预设。",
        enabled=True,
        is_system=True,
    )


def _seeded_glossary_en() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_GLOSSARY_EN_ID,
        name="Default",
        kind=PromptKind.GLOSSARY,
        system_prompt=_GLOSSARY_SYSTEM_EN,
        suffix_prompt=_GLOSSARY_SUFFIX_EN,
        thinking_prompt=_GLOSSARY_THINKING_EN,
        description="Default glossary extraction preset.",
        enabled=True,
        is_system=True,
    )


def seeded_presets(kind: PromptKind) -> tuple[PromptPreset, ...]:
    """Return all presets seeded into a fresh prompts.<kind>.json."""
    if kind is PromptKind.TRANSLATION:
        return (_seeded_translation_zh(), _seeded_translation_en())
    return (_seeded_glossary_zh(), _seeded_glossary_en())


def default_preset(kind: PromptKind) -> PromptPreset:
    """Return the primary default preset (used when nothing is selected)."""
    return seeded_presets(kind)[0]


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

    On a missing or empty file the store returns the full seeded library
    (currently a Chinese-instruction primary plus an English-instruction
    sibling). Once a file exists, only the primary default is re-injected
    if the user explicitly removed it; other seeded variants are left as
    the user shaped them.
    """

    path: Path
    kind: PromptKind

    def load(self) -> tuple[PromptPreset, ...]:
        if not self.path.exists():
            return seeded_presets(self.kind)
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Cannot read prompt preset file: {self.path}") from exc
        if not raw.strip():
            return seeded_presets(self.kind)
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
        stored = [PromptPreset.from_dict(item) for item in payload]
        wrong_kind = [p.id for p in stored if p.kind is not self.kind]
        if wrong_kind:
            raise ValueError(
                f"Preset kind mismatch in {self.path}: {wrong_kind!r} != {self.kind.value}"
            )
        # System presets (the seeded ZH/EN defaults) are sourced from
        # code, not disk: any time the source-tree prompts are edited
        # we want users to see the new copy immediately. The on-disk
        # version is fully ignored for system ids — including
        # ``enabled`` — so users can neither break a system preset by
        # toggling it off nor freeze its content at an old revision.
        # Custom presets pass through unchanged.
        seeded_by_id = {preset.id: preset for preset in seeded_presets(self.kind)}
        merged: list[PromptPreset] = []
        seen_seeded: set[str] = set()
        for preset in stored:
            if preset.id in seeded_by_id:
                merged.append(seeded_by_id[preset.id])
                seen_seeded.add(preset.id)
            else:
                merged.append(preset)
        # Re-inject any seeded presets that the user removed (or that
        # never made it into the on-disk store), preserving their
        # canonical order at the front.
        missing = [sp for sid, sp in seeded_by_id.items() if sid not in seen_seeded]
        if missing:
            merged = [*missing, *merged]
        return tuple(merged)

    def save(self, presets: Sequence[PromptPreset]) -> None:
        wrong_kind = [p.id for p in presets if p.kind is not self.kind]
        if wrong_kind:
            raise ValueError(
                f"Preset kind mismatch when saving: {wrong_kind!r} != {self.kind.value}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [preset.to_dict() for preset in presets]
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
        Otherwise the primary seeded default for this kind is returned.
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
    "seeded_presets",
    "DEFAULT_TRANSLATION_PRESET_ID",
    "DEFAULT_GLOSSARY_PRESET_ID",
    "DEFAULT_TRANSLATION_EN_ID",
    "DEFAULT_GLOSSARY_EN_ID",
]
