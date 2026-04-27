"""Heuristic confidence checks for translation segments.

Two cheap signals catch most "the model truncated/inflated/dropped this line"
failures without needing a second LLM call:

1. **Length ratio** — ``len(translated) / max(len(source), 1)`` outside the
   configured ``[min_length_ratio, max_length_ratio]`` band is suspicious.
2. **Punctuation delta** — sentence-ending punctuation count should track
   between source and translation. A large absolute delta usually means the
   model collapsed or reorganised sentences.

Defaults are intentionally loose so legitimate cross-language length
differences (Korean → English typically expands; Chinese → English typically
expands further) don't trip the check. The flag itself is opt-in via
``TranslationConfig.enable_confidence_check``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from transoria.domain import Language


_PUNCTUATION_CHARS = (
    # ASCII sentence-ending and clause-separating punctuation.
    ",.;:!?"
    # CJK fullwidth equivalents.
    "，。；：！？、"
    # CJK quotation marks — both Korean/Chinese 「」 and Chinese-novel 《》.
    # When the LLM drops dialogue quotes, the punctuation delta jumps and
    # the segment is flagged for review.
    "「」『』《》〈〉"
    # Ellipsis + dashes that often disappear in translation.
    "…—–"
)


@dataclass(frozen=True)
class ConfidenceVerdict:
    is_low_confidence: bool
    reasons: tuple[str, ...] = ()


def evaluate_segment_confidence(
    source_text: str,
    translated_text: str,
    *,
    min_length_ratio: float,
    max_length_ratio: float,
    max_punctuation_delta: int,
    source_language: Language | None = None,
) -> ConfidenceVerdict:
    reasons: list[str] = []

    if not source_text.strip():
        return ConfidenceVerdict(is_low_confidence=False)
    if not translated_text.strip():
        return ConfidenceVerdict(
            is_low_confidence=True,
            reasons=("empty translation for non-empty source",),
        )

    ratio = len(translated_text) / max(len(source_text), 1)
    if ratio < min_length_ratio:
        reasons.append(f"length ratio {ratio:.2f} < min {min_length_ratio:.2f}")
    elif ratio > max_length_ratio:
        reasons.append(f"length ratio {ratio:.2f} > max {max_length_ratio:.2f}")

    source_punct = _count_punctuation(source_text)
    translated_punct = _count_punctuation(translated_text)
    delta = abs(translated_punct - source_punct)
    if delta > max_punctuation_delta:
        reasons.append(
            f"punctuation delta {delta} > max {max_punctuation_delta}"
        )

    residue_reason = _source_language_residue(translated_text, source_language)
    if residue_reason:
        reasons.append(residue_reason)

    if _too_similar(source_text, translated_text):
        reasons.append("source and translation are too similar")

    return ConfidenceVerdict(
        is_low_confidence=bool(reasons),
        reasons=tuple(reasons),
    )


def _count_punctuation(text: str) -> int:
    return sum(1 for char in text if char in _PUNCTUATION_CHARS)


def _source_language_residue(
    translated_text: str, source_language: Language | None
) -> str | None:
    if source_language is Language.KOREAN and re.search(r"[\uac00-\ud7af]", translated_text):
        return "Korean residue remains in translation"
    if source_language is Language.JAPANESE and re.search(r"[\u3040-\u30ff]", translated_text):
        return "Japanese kana residue remains in translation"
    return None


def _too_similar(source_text: str, translated_text: str) -> bool:
    source = _normalize_for_similarity(source_text)
    translated = _normalize_for_similarity(translated_text)
    if len(source) < 8 or len(translated) < 8:
        return False
    if source == translated:
        return True
    source_chars = set(source)
    translated_chars = set(translated)
    if not source_chars or not translated_chars:
        return False
    overlap = len(source_chars & translated_chars) / len(source_chars | translated_chars)
    return overlap >= 0.92


def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


__all__ = ["ConfidenceVerdict", "evaluate_segment_confidence"]
