"""Shared content prefilter for translation and glossary extraction.

A line of source text is "translation-skippable" when, after stripping
whitespace, it contains no letter characters — only digits, punctuation,
symbols, whitespace, or a fixed identifier. Examples:

- ``""`` — empty paragraph break
- ``"   "`` — whitespace only
- ``"1234"`` — page number
- ``"———"`` — decorative section divider
- ``"..."`` — pure punctuation beat
- ``"(1)"`` — figure label
- ``"🎉"`` — emoji line
- ``"ISBN | 979-11-01-87478-2"`` — fixed identifier line
- ``"010-xxxx-xxxx"`` — masked phone number
- ``"www.example.com"`` — standalone URL
- ``"@example_account"`` — standalone social handle

These lines have no meaningful content for either translation or
glossary extraction, so the orchestrator skips them: the writer keeps
the original line verbatim and no LLM call is ever made. For typical
novels this trims a few percent of requests; for game text or subtitles
with many numeric IDs the savings can reach 15-20%.

The detection is letter-based via ``unicodedata.category``: any
character whose category starts with ``"L"`` (any letter, in any
script) makes the line translatable. This avoids hand-curated
punctuation lists and works across Latin, CJK, Cyrillic, Hangul, etc.
"""

from __future__ import annotations

import re
import unicodedata

from transoria.domain import Language


_CJK_IDEOGRAPH_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\uffa0-\uffdc]")
_JAPANESE_RE = re.compile(
    r"[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f"
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
)
_LATIN_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_ISBN_PREFIX_RE = re.compile(r"^\s*ISBN(?:-1[03])?\s*(?:[:：|｜]\s*)?", re.IGNORECASE)
_MASKED_KOREAN_PHONE_RE = re.compile(
    r"^(?=[0-9Xx*\s-]*[Xx*])"
    r"(?:01[016789]|02|0[3-6][1-5])[-\s][0-9Xx*]{3,4}[-\s][0-9Xx*]{4}$"
)
_STANDALONE_URL_RE = re.compile(
    r"^(?:https?://|www\.)"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
    r"(?::[0-9]{1,5})?(?:[/?#][^\s]*)?$",
    re.IGNORECASE,
)
_STANDALONE_SOCIAL_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_][A-Za-z0-9_.-]*$")

_LANGUAGE_SCRIPT_RE: dict[Language, re.Pattern[str]] = {
    Language.KOREAN: _HANGUL_RE,
    Language.CHINESE_SIMPLIFIED: _CJK_IDEOGRAPH_RE,
    Language.CHINESE_TRADITIONAL: _CJK_IDEOGRAPH_RE,
    Language.ENGLISH: _LATIN_RE,
    Language.JAPANESE: _JAPANESE_RE,
    Language.RUSSIAN: _CYRILLIC_RE,
    Language.ARABIC: _ARABIC_RE,
    Language.GERMAN: _LATIN_RE,
    Language.FRENCH: _LATIN_RE,
    Language.POLISH: _LATIN_RE,
    Language.SPANISH: _LATIN_RE,
    Language.ITALIAN: _LATIN_RE,
    Language.PORTUGUESE: _LATIN_RE,
    Language.HUNGARIAN: _LATIN_RE,
    Language.TURKISH: _LATIN_RE,
    Language.THAI: _THAI_RE,
    Language.INDONESIAN: _LATIN_RE,
    Language.VIETNAMESE: _LATIN_RE,
}


def is_translation_skippable(text: str) -> bool:
    """True if the line carries no translatable content.

    Empty / whitespace-only lines and lines made up entirely of digits,
    punctuation, symbols, or whitespace return ``True``. Any letter
    character (Latin, CJK, Hangul, Cyrillic, etc.) makes the line
    translatable and the function returns ``False``.
    """

    stripped = text.strip()
    if not stripped:
        return True
    if is_fixed_identifier_line(stripped):
        return True
    for char in stripped:
        if unicodedata.category(char).startswith("L"):
            return False
    return True


def is_fixed_identifier_line(text: str) -> bool:
    stripped = text.strip()
    if (
        _MASKED_KOREAN_PHONE_RE.fullmatch(stripped)
        or _STANDALONE_URL_RE.fullmatch(stripped)
        or _STANDALONE_SOCIAL_HANDLE_RE.fullmatch(stripped)
    ):
        return True
    match = _ISBN_PREFIX_RE.match(stripped)
    if match is None:
        return False
    compact = re.sub(r"[-\s]", "", stripped[match.end() :])
    return bool(re.fullmatch(r"(?:97[89]\d{10}|\d{9}[\dXx])", compact))


def should_translate_for_language(
    text: str, *, source_language: Language, target_language: Language
) -> bool:
    """False when a segment is already clearly in the target script."""

    stripped = text.strip()
    if not stripped or is_fixed_identifier_line(stripped):
        return False
    source_re = _LANGUAGE_SCRIPT_RE.get(source_language)
    target_re = _LANGUAGE_SCRIPT_RE.get(target_language)
    if source_re is None or target_re is None or source_re.pattern == target_re.pattern:
        return not is_translation_skippable(stripped)
    if source_re.search(stripped):
        return True
    if target_re.search(stripped):
        return False
    return not is_translation_skippable(stripped)


def contains_source_language_script(text: str, source_language: Language) -> bool:
    """True when text carries a script signal for the configured source language."""

    stripped = text.strip()
    if not stripped or is_fixed_identifier_line(stripped):
        return False
    source_re = _LANGUAGE_SCRIPT_RE.get(source_language)
    if source_re is None:
        return not is_translation_skippable(stripped)
    return bool(source_re.search(stripped))


__all__ = [
    "contains_source_language_script",
    "is_fixed_identifier_line",
    "is_translation_skippable",
    "should_translate_for_language",
]
