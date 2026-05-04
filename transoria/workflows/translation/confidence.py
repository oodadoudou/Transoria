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


# Korean Hangul Syllables \u2014 full Korean words/phrases. Translation
# output should never carry these; any occurrence = model laziness.
#   U+AC00-U+D7AF  Hangul Syllables (\uc548\ub155\ud558\uc138\uc694 etc)
_KOREAN_SYLLABLES_PATTERN = re.compile(r"[\uac00-\ud7af]")

# Korean Jamo blocks \u2014 single letters used as cultural references in
# Chinese text (sorting "from \u3131", shape "\u3137-shape", chat "\u314b\u314b").
# Flag only when they saturate the line, not when sprinkled in long text.
#   U+1100-U+11FF  Hangul Jamo
#   U+3130-U+318F  Hangul Compatibility Jamo
#   U+A960-U+A97F  Hangul Jamo Extended-A
#   U+D7B0-U+D7FF  Hangul Jamo Extended-B
#   U+FFA0-U+FFDC  Halfwidth Hangul Jamo
_KOREAN_JAMO_PATTERN = re.compile(
    r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\ud7b0-\ud7ff\uffa0-\uffdc]"
)

# Japanese kana \u2014 translation output should never contain these.
# Excludes punctuation-class chars that legitimately appear in Chinese
# text: U+309B/U+309C (dakuten/handakuten), U+30FB (fullwidth middle
# dot \u30fb), U+30FC (long-sound mark \u30fc), U+FF65 (halfwidth middle dot \uff65),
# and the halfwidth CJK-punctuation block U+FF61-U+FF64 (\uff61\uff62\uff63\uff64).
#   U+3040-U+309A, U+309D-U+309F  Hiragana (sans dakuten marks)
#   U+30A0-U+30FA, U+30FD-U+30FF  Katakana (sans middle dot, long mark)
#   U+31F0-U+31FF                 Katakana Phonetic Extensions
#   U+FF66-U+FF9F                 Halfwidth Katakana letters only
_JAPANESE_KANA_PATTERN = re.compile(
    "["
    "\u3040-\u309a"   # Hiragana (excl. \u309b dakuten, \u309c handakuten)
    "\u309d-\u309f"   # Hiragana iteration marks, digraph yori
    "\u30a0-\u30fa"   # Katakana (excl. \u30fb \u30fb, \u30fc \u30fc)
    "\u30fd-\u30ff"   # Katakana iteration marks
    "\u31f0-\u31ff"   # Katakana Phonetic Extensions
    "\uff66-\uff9f"   # Halfwidth Katakana letters (excl. \uff65 \uff65)
    "]"
)

# Threshold for Compat-Jamo-only residue. Empirically a chat laze
# (\u314b\u314b\u314b\u314b\u314b\u314b\u314b) sits at \u226540% jamo, while a single-letter cultural
# reference is \u22645% of any reasonably-long sentence.
_JAMO_RATIO_THRESHOLD = 0.10


def _source_language_residue(
    translated_text: str, source_language: Language | None
) -> str | None:
    if source_language is Language.KOREAN:
        # Hangul Syllables = real Korean words, never legitimate.
        if _KOREAN_SYLLABLES_PATTERN.search(translated_text):
            return "Korean residue remains in translation"
        # Compat-Jamo cluster = chat slang or saturating residue.
        if _ratio(_KOREAN_JAMO_PATTERN, translated_text) > _JAMO_RATIO_THRESHOLD:
            return "Korean residue remains in translation"
    elif source_language is Language.JAPANESE:
        if _JAPANESE_KANA_PATTERN.search(translated_text):
            return "Japanese kana residue remains in translation"
    return None


def _ratio(pattern: re.Pattern[str], text: str) -> float:
    if not text:
        return 0.0
    hits = len(pattern.findall(text))
    return hits / len(text)


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
