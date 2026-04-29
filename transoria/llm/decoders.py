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


def _preprocess(raw: str) -> str:
    text = _THINKING_BLOCK_PATTERN.sub("", raw)
    match = _FENCED_BLOCK_PATTERN.search(text)
    if match:
        text = match.group(1)
    return text.strip()


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
    """

    entries: list[GlossaryEntry] = []
    issues: list[DecodeIssue] = []

    for line in _preprocess(raw).splitlines():
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
