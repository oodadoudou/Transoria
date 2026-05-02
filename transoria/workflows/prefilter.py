"""Shared content prefilter for translation and glossary extraction.

A line of source text is "translation-skippable" when, after stripping
whitespace, it contains no letter characters — only digits, punctuation,
symbols, or whitespace. Examples:

- ``""`` — empty paragraph break
- ``"   "`` — whitespace only
- ``"1234"`` — page number
- ``"———"`` — decorative section divider
- ``"..."`` — pure punctuation beat
- ``"(1)"`` — figure label
- ``"🎉"`` — emoji line

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

import unicodedata


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
    for char in stripped:
        if unicodedata.category(char).startswith("L"):
            return False
    return True


__all__ = ["is_translation_skippable"]
