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
_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_BACKTICK_PATTERN = re.compile(r"`([^`]+)`")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_LEADING_INDEX_PATTERN = re.compile(r"^\d+[\.\)、]?$")

# Header keywords used to identify which Markdown column carries each
# field. Lower-cased, ASCII-folded comparisons. Order in each set does
# not matter — first matching column wins.
_HEADER_KEYWORDS_SRC: frozenset[str] = frozenset(
    {
        "src",
        "source",
        "原文",
        "原词",
        "korean",
        "japanese",
        "english",
        "韩文原文",
        "日文原文",
        "英文原文",
        "韩语",
        "日语",
        "英语",
        "韩文",
        "日文",
        "英文",
    }
)
_HEADER_KEYWORDS_DST: frozenset[str] = frozenset(
    {
        "dst",
        "target",
        "translation",
        "translated",
        "译文",
        "译名",
        "翻译",
        "中文译名",
        "中文翻译",
        "中文",
        "chinese",
    }
)
_HEADER_KEYWORDS_INFO: frozenset[str] = frozenset(
    {
        "type",
        "category",
        "kind",
        "类型",
        "类别",
        "分类",
        "大类",
        "细分类",
        "实体类型",
        "所属分类",
        "info",
        "备注",
        "说明",
        "note",
        "remark",
        "comment",
    }
)


def _preprocess(raw: str) -> str:
    text = _THINKING_BLOCK_PATTERN.sub("", raw)
    match = _FENCED_BLOCK_PATTERN.search(text)
    if match:
        text = match.group(1)
    return text.strip()


def _strip_table_cell(value: str) -> str:
    out = _BOLD_PATTERN.sub(r"\1", value)
    out = _BACKTICK_PATTERN.sub(r"\1", out)
    out = _HTML_TAG_PATTERN.sub("", out)
    return out.strip()


def _split_table_cells(line: str) -> list[str] | None:
    match = _TABLE_ROW_PATTERN.match(line)
    if not match:
        return None
    raw_cells = match.group(1).split("|")
    return [_strip_table_cell(c) for c in raw_cells]


def _detect_header_columns(cells: list[str]) -> dict[str, int] | None:
    """Map a header row's cells to ``{src,dst,info}`` column indices.

    Returns ``None`` if at least ``src`` and ``dst`` cannot be matched.
    The header check is keyword-based, not positional, so column orders
    like ``| 分类 | 原文 | 译名 | 备注 |`` map cleanly. ``info`` is
    optional — when absent the salvaged entries get an empty ``info``.
    """

    mapping: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        token = cell.strip().lower()
        if not token:
            continue
        if "src" not in mapping and token in _HEADER_KEYWORDS_SRC:
            mapping["src"] = idx
        elif "dst" not in mapping and token in _HEADER_KEYWORDS_DST:
            mapping["dst"] = idx
        elif "info" not in mapping and token in _HEADER_KEYWORDS_INFO:
            mapping["info"] = idx
    if "src" in mapping and "dst" in mapping and mapping["src"] != mapping["dst"]:
        return mapping
    return None


def _salvage_glossary_table(text: str) -> tuple[GlossaryEntry, ...]:
    """Recover entries from Markdown tables when the LLM ignored JSONLINE.

    The LLM's column order is unstable, so this is strictly header-driven:
    every salvaged row uses the most recent header's column mapping. Rows
    that appear before any recognised header are discarded — better to
    drop legitimate data than to guess and produce ``(src='1', dst='男性
    角色')`` corruption from a row-numbered table.

    A blank line resets the column mapping so a follow-up table with a
    different header layout doesn't inherit the previous mapping.
    """

    entries: list[GlossaryEntry] = []
    column_map: dict[str, int] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip():
            column_map = None
            continue
        if _TABLE_SEPARATOR_PATTERN.match(raw_line):
            continue
        cells = _split_table_cells(raw_line)
        if cells is None:
            continue
        header = _detect_header_columns(cells)
        if header is not None:
            column_map = header
            continue
        if column_map is None:
            continue
        non_empty = [c for c in cells if c]
        # A row whose first cell is just a numeric index ("1", "2.")
        # under a header that doesn't map to src column 0 is a sign the
        # LLM injected a leading row counter — drop those.
        if column_map.get("src", 0) != 0 and non_empty and _LEADING_INDEX_PATTERN.match(
            non_empty[0]
        ):
            cells = cells[1:]
        try:
            src = cells[column_map["src"]]
            dst = cells[column_map["dst"]]
        except IndexError:
            continue
        info = ""
        info_idx = column_map.get("info")
        if info_idx is not None and 0 <= info_idx < len(cells):
            info = cells[info_idx]
        if not src or not dst:
            continue
        entries.append(GlossaryEntry(src=src, dst=dst, info=info))
    return tuple(entries)


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

    When JSONLINE parsing yields zero entries, the decoder tries a
    header-driven Markdown-table salvage (``_salvage_glossary_table``).
    The salvage refuses to guess column order — every salvaged row uses
    the column mapping derived from the most recent recognised header,
    so a column-shuffled response cannot produce a misaligned entry.
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
        salvaged = _salvage_glossary_table(preprocessed)
        if salvaged:
            entries.extend(salvaged)

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
