"""Tolerant JSONL decoders for translation and glossary extraction responses.

Both decoders share the same pre-processing pipeline:

1. Strip a single fenced code block (``\u0060\u0060\u0060jsonline`` / ``\u0060\u0060\u0060json`` / ``\u0060\u0060\u0060``).
2. Strip leading ``<why>...</why>`` reasoning blocks emitted by thinking models.
3. Iterate over non-empty lines.
4. Try ``json.loads``; on failure, fall back to ``json_repair.loads``.
5. Skip rows that cannot be coerced into the target shape.

Invalid rows are reported via ``DecodeReport`` so the caller can log them and
decide whether to retry or fail. The decoders themselves never raise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import json_repair


@dataclass(frozen=True)
class TranslationLine:
    index: int
    text: str


@dataclass(frozen=True)
class GlossaryEntry:
    src: str
    dst: str
    info: str


@dataclass(frozen=True)
class DecodeIssue:
    line: str
    reason: str


@dataclass(frozen=True)
class TranslationDecodeResult:
    lines: tuple[TranslationLine, ...]
    issues: tuple[DecodeIssue, ...]


@dataclass(frozen=True)
class GlossaryDecodeResult:
    entries: tuple[GlossaryEntry, ...]
    issues: tuple[DecodeIssue, ...]


_FENCED_BLOCK_PATTERN = re.compile(
    r"```(?:jsonline|jsonl|json)?\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
_THINKING_BLOCK_PATTERN = re.compile(r"<why>.*?</why>", re.DOTALL | re.IGNORECASE)
_TABLE_ROW_PATTERN = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|[\s:|\-]+\|\s*$")
# Header cells we should never treat as src/dst — common bilingual table
# headers seen in DeepSeek / Gemini Markdown fallback output.
_TABLE_HEADER_TOKENS = {
    "原文",
    "译文",
    "类型",
    "类别",
    "分类",
    "子类别",
    "备注",
    "韩文原文",
    "中文译名",
    "src",
    "dst",
    "type",
    "category",
    "source",
    "translation",
    "korean",
    "chinese",
}


def _preprocess(raw: str) -> str:
    text = _THINKING_BLOCK_PATTERN.sub("", raw)
    match = _FENCED_BLOCK_PATTERN.search(text)
    if match:
        text = match.group(1)
    return text.strip()


def _try_parse_glossary_markdown_table(
    text: str,
) -> tuple[tuple[GlossaryEntry, ...], tuple[DecodeIssue, ...]]:
    """Recover glossary entries from Markdown table rows.

    Last-resort fallback when the LLM returns ``| src | dst | type |``
    rows instead of JSONLINE. Drops separator rows and header-looking
    rows; otherwise treats the first three non-empty cells as
    ``(src, dst, info)``.
    """

    entries: list[GlossaryEntry] = []
    issues: list[DecodeIssue] = []
    for line in text.splitlines():
        if _TABLE_SEPARATOR_PATTERN.match(line):
            continue
        match = _TABLE_ROW_PATTERN.match(line)
        if not match:
            continue
        # `| a | b | c |` → ["", " a ", " b ", " c ", ""] → strip and drop empties.
        raw_cells = match.group(1).split("|")
        cells = [c.strip() for c in raw_cells]
        cells = [c for c in cells if c]
        if len(cells) < 3:
            continue
        # Drop bold markers, html tags, and embedded backticks that
        # leak into table cells.
        cleaned = [_strip_table_cell(c) for c in cells[:4]]
        src, dst, info = cleaned[0], cleaned[1], cleaned[2]
        if not src or not dst:
            continue
        if src.lower() in _TABLE_HEADER_TOKENS or dst.lower() in _TABLE_HEADER_TOKENS:
            continue
        entries.append(GlossaryEntry(src=src, dst=dst, info=info))
    return tuple(entries), tuple(issues)


_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_BACKTICK_PATTERN = re.compile(r"`([^`]+)`")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _strip_table_cell(value: str) -> str:
    out = _BOLD_PATTERN.sub(r"\1", value)
    out = _BACKTICK_PATTERN.sub(r"\1", out)
    out = _HTML_TAG_PATTERN.sub("", out)
    return out.strip()


def _parse_json_line(line: str) -> object | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        try:
            return json_repair.loads(line)
        except (ValueError, TypeError):
            return None


def decode_translation_jsonl(raw: str) -> TranslationDecodeResult:
    """Parse a JSONL response of the form ``{"<index>": "<text>"}`` per line.

    The model returns one object per line where the single key is the integer
    line index and the value is the translated text. Index parsing is lenient:
    string keys that contain digits are coerced to ``int``; non-numeric keys
    are reported as issues and skipped.
    """

    lines: list[TranslationLine] = []
    issues: list[DecodeIssue] = []
    seen_indices: set[int] = set()

    for line in _preprocess(raw).splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped:
            continue
        parsed = _parse_json_line(stripped)
        if not isinstance(parsed, dict) or len(parsed) != 1:
            issues.append(DecodeIssue(line=line, reason="not a single-key JSON object"))
            continue
        key, value = next(iter(parsed.items()))
        try:
            index = int(str(key).strip())
        except (TypeError, ValueError):
            issues.append(DecodeIssue(line=line, reason=f"non-numeric index: {key!r}"))
            continue
        if index in seen_indices:
            issues.append(DecodeIssue(line=line, reason=f"duplicate index: {index}"))
            continue
        seen_indices.add(index)
        lines.append(TranslationLine(index=index, text=str(value)))

    return TranslationDecodeResult(lines=tuple(lines), issues=tuple(issues))


def decode_glossary_jsonl(raw: str) -> GlossaryDecodeResult:
    """Parse a JSONL response of the form ``{"src":..,"dst":..,"type":..}``.

    The decoder accepts both ``type`` and ``info`` keys and normalises to
    ``info`` so downstream code only ever sees one name. Rows missing ``src``
    or ``dst`` are reported as issues and skipped; rows missing ``type`` /
    ``info`` pass through with an empty ``info``.

    When JSONLINE parsing yields zero entries but the response still has
    Markdown-table-shaped lines (``| src | dst | type | …``), the decoder
    falls back to table parsing as a salvage path — covers DeepSeek/Gemini
    runs that ignore JSONLINE instructions.
    """

    entries: list[GlossaryEntry] = []
    issues: list[DecodeIssue] = []
    preprocessed = _preprocess(raw)

    for line in preprocessed.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped:
            continue
        parsed = _parse_json_line(stripped)
        if not isinstance(parsed, dict):
            issues.append(DecodeIssue(line=line, reason="not a JSON object"))
            continue
        src = parsed.get("src")
        dst = parsed.get("dst")
        info = parsed.get("info", parsed.get("type", ""))
        if not isinstance(src, str) or not src.strip():
            issues.append(DecodeIssue(line=line, reason="missing or empty 'src'"))
            continue
        if not isinstance(dst, str) or not dst.strip():
            issues.append(DecodeIssue(line=line, reason="missing or empty 'dst'"))
            continue
        entries.append(
            GlossaryEntry(
                src=src.strip(),
                dst=dst.strip(),
                info=str(info).strip(),
            )
        )

    if not entries:
        salvaged, _ = _try_parse_glossary_markdown_table(preprocessed)
        if salvaged:
            entries = list(salvaged)
            # Keep the original JSONLINE issues for visibility; the
            # salvage worked but the raw content was still off-spec.

    return GlossaryDecodeResult(entries=tuple(entries), issues=tuple(issues))


__all__ = [
    "DecodeIssue",
    "GlossaryDecodeResult",
    "GlossaryEntry",
    "TranslationDecodeResult",
    "TranslationLine",
    "decode_glossary_jsonl",
    "decode_translation_jsonl",
]
