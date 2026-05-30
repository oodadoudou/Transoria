"""Per-segment preprocessing and postprocessing for translation.

Given a source segment string, the preprocessor produces:

- a ``prompt_text`` ready to send to the LLM (with text-preserve spans masked
  by sentinels and pre-replacement rules applied), and
- a ``ProtectionMap`` recording each masked span so the postprocessor can
  restore the originals after the response comes back.

The postprocessor then takes the LLM's translated text plus the
``ProtectionMap`` and the original segment metadata, restores protected spans,
applies post-replacement rules, and re-attaches the leading/trailing
whitespace that was stripped before sending.

Design notes:

- Sentinels use the form ``\u2061__TPRES_<n>__\u2061`` — invisible function
  application characters wrap an ASCII-only marker. The model is far less
  likely to translate, split, or paraphrase them than a bare ``__X__`` token.
- Text-preserve rules apply to the *current* segment only. If two segments
  contain the same protected span, each gets its own sentinel. This keeps
  per-subtask state self-contained.
- Pre-replacements run *after* protection so a pre-replacement that targets a
  preserved string never collides with the sentinel.
- Post-replacements run *after* restoration so a rule that targets a restored
  literal still fires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from transoria.workflows.translation.rules import (
    ReplacementRule,
    TextPreserveRule,
)


_SENTINEL_PREFIX = "\u2061__TPRES_"
_SENTINEL_SUFFIX = "__\u2061"
_SENTINEL_PATTERN = re.compile(
    re.escape(_SENTINEL_PREFIX) + r"(\d+)" + re.escape(_SENTINEL_SUFFIX)
)

# Strip invisible/filler chars before deciding whether a segment should be
# translated. Cf-class marks are common EPUB DRM noise; U+3164 can appear as
# publisher blank-line filler between blocks. These survive normal whitespace
# stripping and fool language/confidence checks. U+2061 (FUNCTION APPLICATION)
# is deliberately excluded \u2014 it's reused inside our protection sentinels.
_DRM_INVISIBLE_PATTERN = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060\u2062-\u2064\u206a-\u206f\u3164\ufeff]+"
)


@dataclass(frozen=True)
class ProtectionMap:
    """Sentinel → original-text mapping for one segment."""

    spans: tuple[str, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not self.spans


@dataclass(frozen=True)
class PreprocessedSegment:
    """Result of running a single segment through the preprocessor."""

    prompt_text: str
    protection: ProtectionMap
    leading_whitespace: str = ""
    trailing_whitespace: str = ""


def preprocess_segment(
    source_text: str,
    *,
    text_preserve_rules: Iterable[TextPreserveRule] = (),
    pre_replacements: Iterable[ReplacementRule] = (),
) -> PreprocessedSegment:
    """Strip DRM invisibles + whitespace, mask preserved spans, apply pre-replacements."""

    cleaned = _DRM_INVISIBLE_PATTERN.sub("", source_text)
    leading, body, trailing = _split_whitespace(cleaned)
    masked, protection = _mask_protected_spans(body, text_preserve_rules)
    rewritten = _apply_replacements(masked, pre_replacements)
    return PreprocessedSegment(
        prompt_text=rewritten,
        protection=protection,
        leading_whitespace=leading,
        trailing_whitespace=trailing,
    )


def postprocess_segment(
    translated_text: str,
    *,
    protection: ProtectionMap,
    leading_whitespace: str = "",
    trailing_whitespace: str = "",
    post_replacements: Iterable[ReplacementRule] = (),
) -> str:
    """Restore protected spans, apply post-replacements, re-attach whitespace."""

    restored = _restore_sentinels(translated_text, protection)
    rewritten = _apply_replacements(restored, post_replacements)
    return f"{leading_whitespace}{rewritten}{trailing_whitespace}"


def _split_whitespace(text: str) -> tuple[str, str, str]:
    """Split into ``(leading, body, trailing)`` whitespace-free body."""

    if not text:
        return "", "", ""
    leading_match = re.match(r"\s*", text)
    leading = leading_match.group(0) if leading_match else ""
    body_with_trailing = text[len(leading):]
    if not body_with_trailing:
        return leading, "", ""
    trailing_match = re.search(r"\s*$", body_with_trailing)
    trailing = trailing_match.group(0) if trailing_match else ""
    body = (
        body_with_trailing[: -len(trailing)] if trailing else body_with_trailing
    )
    return leading, body, trailing


def _mask_protected_spans(
    body: str, rules: Iterable[TextPreserveRule]
) -> tuple[str, ProtectionMap]:
    spans: list[str] = []
    masked = body
    for rule in rules:
        if not rule.enabled or not rule.pattern:
            continue
        try:
            pattern = re.compile(rule.pattern)
        except re.error:
            continue
        masked = pattern.sub(lambda match: _push_sentinel(match.group(0), spans), masked)
    return masked, ProtectionMap(spans=tuple(spans))


def _push_sentinel(value: str, spans: list[str]) -> str:
    spans.append(value)
    return f"{_SENTINEL_PREFIX}{len(spans) - 1}{_SENTINEL_SUFFIX}"


def _restore_sentinels(text: str, protection: ProtectionMap) -> str:
    if protection.is_empty():
        return text

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if 0 <= index < len(protection.spans):
            return protection.spans[index]
        return match.group(0)

    return _SENTINEL_PATTERN.sub(replace, text)


def _apply_replacements(text: str, rules: Iterable[ReplacementRule]) -> str:
    if not text:
        return text
    out = text
    for rule in rules:
        if not rule.enabled or not rule.src:
            continue
        if rule.regex:
            flags = 0 if rule.case_sensitive else re.IGNORECASE
            try:
                out = re.sub(
                    rule.src,
                    _normalize_regex_replacement(rule.dst),
                    out,
                    flags=flags,
                )
            except re.error:
                continue
            continue
        if rule.case_sensitive:
            out = out.replace(rule.src, rule.dst)
            continue
        # Case-insensitive plain replacement: use escaped regex with IGNORECASE.
        out = re.sub(re.escape(rule.src), rule.dst, out, flags=re.IGNORECASE)
    return out


def _normalize_regex_replacement(template: str) -> str:
    if "$" not in template:
        return template
    parts: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char == "\\" and index + 1 < len(template) and template[index + 1] == "$":
            parts.append("$")
            index += 2
            continue
        if char != "$":
            parts.append(char)
            index += 1
            continue
        if index + 1 < len(template) and template[index + 1] == "$":
            parts.append("$")
            index += 2
            continue
        if index + 1 < len(template) and template[index + 1] == "{":
            close = template.find("}", index + 2)
            group = template[index + 2 : close] if close != -1 else ""
            if group.isdigit():
                parts.append(rf"\g<{group}>")
                index = close + 1
                continue
        cursor = index + 1
        while cursor < len(template) and template[cursor].isdigit():
            cursor += 1
        if cursor > index + 1:
            parts.append(rf"\g<{template[index + 1:cursor]}>")
            index = cursor
            continue
        parts.append("$")
        index += 1
    return "".join(parts)


def strip_drm_invisibles(text: str) -> str:
    return _DRM_INVISIBLE_PATTERN.sub("", text)


__all__ = [
    "PreprocessedSegment",
    "ProtectionMap",
    "preprocess_segment",
    "postprocess_segment",
    "strip_drm_invisibles",
]
