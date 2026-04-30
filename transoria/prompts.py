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

    Order: ``system_prompt`` → ``thinking_prompt`` (only when ``thinking`` is
    ``True`` and the preset has one) → ``suffix_prompt``. Only the named
    placeholders in ``_PLACEHOLDER_NAMES`` are substituted, so literal braces
    in JSONL examples (e.g. ``{"<INDEX>":"<Translated Text>"}``) survive
    untouched.
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
# Default presets
# ---------------------------------------------------------------------------

DEFAULT_TRANSLATION_PRESET_ID = "default-translation-zh"
DEFAULT_GLOSSARY_PRESET_ID = "default-glossary-zh"
DEFAULT_TRANSLATION_EN_ID = "default-translation-en"
DEFAULT_GLOSSARY_EN_ID = "default-glossary-en"


_TRANSLATION_SYSTEM_ZH = """\
角色：资深文学翻译家。
任务：将原文译为 {target_language}，精准、流畅、忠实。

# Prime Directive：语义绝对忠实
所有润色、调整、措辞选择必须建立在 100% 忠实原文语义的基础上。
- 允许：调整语序；选用更精准的近义词；补充逻辑连接词。
- 禁止：增加原文不存在的动作 / 情感 / 评价；删减有效信息；为文笔而扭曲原意。

# 风格
- 克制、精准、连贯。日常物品用客观形容词，避免堆砌大词或强凑成语。
- 用清爽书面语去翻译腔；利用连接词把碎句合成流畅长句。
- 对话符合 {target_language} 自然口语，按 {target_language} 句法习惯重组语序。

# 语境与文化
- 识别敬语 / 平语等语体切换，按 {target_language} 习惯处理。
- 称呼按上下文本地化，避免机械直译。
- 文化专有名词优先意译；难以直译的，简短解释保证理解无障碍。
- 拟声词、语气词、情绪符号都要译。

# 格式
- 保留原文段落结构。被强切的碎句可在句内或行间合并以保证连贯。
- 控制字符（变量、占位符、转义、HTML/XML 标签、ID、URL、文件路径、颜色码等）原样保留，不译。
- 标点按 {target_language} 出版习惯排版。

# 输出要求
- 行数严格对应原文，禁止合并 / 拆分。
- 不评价、不淡化、不回避；还原原文风格与细节。"""

_TRANSLATION_SYSTEM_EN = """\
Role: Senior literary translator.
Task: Translate the source into {target_language} — precise, fluent, faithful.

# Prime Directive: Absolute semantic fidelity
Every polish, reorder, and word choice must rest on 100% faithfulness to the source meaning.
- Allowed: re-order; substitute more precise synonyms; insert logical connectors.
- Forbidden: add actions / feelings / judgments not in the source; drop valid information; distort meaning for stylistic flourish.

# Style
- Restrained, precise, coherent. Use objective adjectives for ordinary objects; avoid grandiose phrasing or forced idioms.
- Avoid translationese: use clean, idiomatic prose; merge fragmented sentences with connectors.
- Render dialogue in natural spoken {target_language}, restructured to native syntax.

# Context & culture
- Detect honorific / plain register switches; render per {target_language} norms.
- Localize forms of address by context; do not transliterate mechanically.
- Prefer meaning-based translation for culture-specific terms; add a brief gloss when a literal rendering would obscure meaning.
- Translate onomatopoeia, interjections, and emotional symbols.

# Format
- Preserve the source paragraph structure. Lines fragmented for layout may be merged within a paragraph for coherence.
- Preserve control sequences verbatim (variables, placeholders, escapes, HTML/XML tags, IDs, URLs, file paths, color codes, etc.) — do not translate them.
- Punctuate per {target_language} publishing conventions.

# Output requirements
- Line count must exactly match the source — never merge or split lines.
- Do not editorialize, sanitize, or evade; reproduce the source's style and detail."""

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

_TRANSLATION_THINKING_ZH = """\
在输出译文之前，先在 <why>...</why> 标签内进行结构化思考：
<why>
[全局语境]：用一句话概括原文的语境、语气与情感基调，识别可能的潜台词或特殊表达。
[核心约束]：重申当前文本最关键的 1-2 条"红线"规则。
[难点处理]：挑出 3-5 个最难翻译的术语、短语或句式，用 `原文 -> 译文（理由）` 的格式简述思路。
</why>"""

_TRANSLATION_THINKING_EN = """\
Before outputting the results, perform **structured thinking** within <why>...</why> tags:
<why>
[Global Context]: Summarize the source's context, tone, and emotional undertones in one sentence; identify potential subtext or special expressions.
[Core Constraints]: Restate the 1-2 most critical "red line" rules for the current text.
[Edge Cases]: Pick 3-5 of the hardest terms, phrases, or sentence structures, briefly outlining the logic in `Source -> Translation (Reasoning)` format.
</why>"""


_GLOSSARY_SYSTEM_ZH = """\
角色：小说世界观数据库架构师。
任务：从原文中提取被命名的、独一无二的实体，并将每条译为 {target_language}。
像严苛的数据库管理员一样思考——拒绝通用、冗余、模糊的条目。

# 原则一：实体资格
仅提取被命名、独一无二的实体。
- "这只是一个 X 吗？"测试：若答案落在某个通用职业 / 物品 / 概念上，不提取。
- 可替代性测试：可被同类实例替代的不算专有命名。

# 原则二：三层过滤（任一层失败即抛弃）
1. 结构性：凡是"A 的 B"型属格短语，一律抛弃。
2. 通用性：现实世界的通用名词 / 动词 / 形容词 / 抽象概念 / 生理 / 解剖学词汇，一律抛弃。
3. 资格性：通过前两层后，再用原则一做最终审查。

# 原则三：合并 / 拆分
"专有名称 + 称号"组合（如"某某伯爵"、"某某公爵"）必须拆成两条：
- 专有名称归入对应的人物分类。
- 称号归入"称号 / 职业"分类。
最终输出每个称号、每个人物只出现一次；不应同时存在「某某」与「某某伯爵」两条。

# 分类
命名实体：男性角色、女性角色、性别未知 / 不适用、神祇 / 传说人物、命名组织、命名地理、命名物品。
世界观设定：称号 / 职业、能力 / 技能、关键事件 / 时期、核心概念 / 法则。"""

_GLOSSARY_SYSTEM_EN = """\
Role: Novel worldbuilding database architect.
Task: Extract named, unique entities from the source and translate each into {target_language}. Think like a strict DB admin — reject generic, redundant, fuzzy entries.

# Principle 1: Entity qualification
Extract only **named, unique** entities.
- "Is this just an X?" test: if the answer is a generic profession, item, or concept, do not extract.
- Replaceability test: a term that can be substituted by another instance of the same kind is not a proper name.

# Principle 2: Three-filter gauntlet (any failure = reject)
1. Structural: any "A of B" possessive phrase is rejected.
2. Generic: everyday real-world nouns / verbs / adjectives / abstract concepts / anatomical / biological terms — all rejected.
3. Qualification: surviving terms pass a final check via Principle 1.

# Principle 3: Merge / split
For "proper name + title" combos (e.g. "Lord X", "Count Y") split into two entries:
- The proper name goes into the appropriate character category.
- The title goes into the "Title / Profession" category.
Each title and each character must appear at most once. Never emit both "X" and "Lord X".

# Categories
Named Entities: Male Character, Female Character, Unknown-Gender Character, Deity / Legend, Named Organization, Named Geography, Named Item.
World Settings: Title / Profession, Ability / Skill, Key Event / Era, Core Concept / Law."""

_GLOSSARY_SUFFIX_ZH = """\
仅在代码块中以 JSONLINE 格式输出，不要任何额外解释或说明：
```jsonline
{"src":"<原文>","dst":"<译文>","type":"<分类>"}
```"""

_GLOSSARY_SUFFIX_EN = """\
Output the result in a code block using JSONLINE, without extra explanation or clarification:
```jsonline
{"src":"<Source Text>","dst":"<Translated Text>","type":"<Category>"}
```"""

_GLOSSARY_THINKING_ZH = """\
在输出结果之前，先在 <why>...</why> 标签内进行结构化思考：
<why>
[全局语境]：用一句话概括原文的题材、世界观与叙事设定，识别潜在的虚构设定。
[核心约束]：重申当前文本最关键的 1-2 条"红线"规则（如三层过滤、合并 / 拆分协议）。
[边界判断]：挑出 3-5 个最难判定的候选词，用 `原文 -> 结果（理由）` 的格式简述判断逻辑。
</why>"""

_GLOSSARY_THINKING_EN = """\
Before outputting the results, perform **structured thinking** within <why>...</why> tags:
<why>
[Global Context]: Summarize the source's genre, worldbuilding, and narrative setting in one sentence; identify potential fictional settings.
[Core Constraints]: Restate the 1-2 most critical "red line" rules for the current text (e.g. three-filter gauntlet, merge/split protocol).
[Edge Cases]: Pick 3-5 of the hardest-to-judge candidates, briefly outlining the logic in `Source -> Result (Reason)` format.
</why>"""


def _seeded_translation_zh() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_TRANSLATION_PRESET_ID,
        name="Default (Chinese)",
        kind=PromptKind.TRANSLATION,
        system_prompt=_TRANSLATION_SYSTEM_ZH,
        suffix_prompt=_TRANSLATION_SUFFIX_ZH,
        thinking_prompt=_TRANSLATION_THINKING_ZH,
        description="默认翻译预设（中文指令版）。语言无关化，可在任意 source/target 语言对上使用。",
        enabled=True,
    )


def _seeded_translation_en() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_TRANSLATION_EN_ID,
        name="Default (English)",
        kind=PromptKind.TRANSLATION,
        system_prompt=_TRANSLATION_SYSTEM_EN,
        suffix_prompt=_TRANSLATION_SUFFIX_EN,
        thinking_prompt=_TRANSLATION_THINKING_EN,
        description="Default translation preset (English instructions). Language-agnostic; works for any source/target language pair.",
        enabled=True,
    )


def _seeded_glossary_zh() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_GLOSSARY_PRESET_ID,
        name="Default (Chinese)",
        kind=PromptKind.GLOSSARY,
        system_prompt=_GLOSSARY_SYSTEM_ZH,
        suffix_prompt=_GLOSSARY_SUFFIX_ZH,
        thinking_prompt=_GLOSSARY_THINKING_ZH,
        description="默认术语提取预设（中文指令版）。聚焦命名实体提取与三层过滤，与原文语言无关。",
        enabled=True,
    )


def _seeded_glossary_en() -> PromptPreset:
    return PromptPreset(
        id=DEFAULT_GLOSSARY_EN_ID,
        name="Default (English)",
        kind=PromptKind.GLOSSARY,
        system_prompt=_GLOSSARY_SYSTEM_EN,
        suffix_prompt=_GLOSSARY_SUFFIX_EN,
        thinking_prompt=_GLOSSARY_THINKING_EN,
        description="Default glossary extraction preset (English instructions). Focused on named-entity extraction with a three-filter qualification gauntlet.",
        enabled=True,
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
