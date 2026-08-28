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
import unicodedata
from collections import Counter
from dataclasses import dataclass

from transoria.domain import Language
from transoria.workflows.prefilter import is_fixed_identifier_line


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
    tags: tuple[str, ...] = ()


TAG_SOURCE_RESIDUE = "source_residue"
TAG_FUNCTION_WORD_RESIDUE = "function_word_residue"
TAG_TARGET_LANGUAGE_WEAK = "target_language_weak"
TAG_MODEL_CHATTER = "model_chatter"
TAG_VERBATIM_ECHO = "verbatim_echo"
TAG_LENGTH_RATIO_ANOMALY = "length_ratio_anomaly"
TAG_PUNCTUATION_ANOMALY = "punctuation_anomaly"
TAG_TRUNCATED = "truncated"

MODEL_ANOMALY_TAGS = frozenset(
    {
        TAG_FUNCTION_WORD_RESIDUE,
        TAG_TARGET_LANGUAGE_WEAK,
        TAG_MODEL_CHATTER,
        TAG_VERBATIM_ECHO,
    }
)


def evaluate_segment_confidence(
    source_text: str,
    translated_text: str,
    *,
    min_length_ratio: float,
    max_length_ratio: float,
    max_punctuation_delta: int,
    source_language: Language | None = None,
    target_language: Language | None = None,
) -> ConfidenceVerdict:
    reasons: list[str] = []
    tags: list[str] = []

    if not source_text.strip():
        return ConfidenceVerdict(is_low_confidence=False)
    if not translated_text.strip():
        return ConfidenceVerdict(
            is_low_confidence=True,
            reasons=("empty translation for non-empty source",),
            tags=(TAG_TARGET_LANGUAGE_WEAK,),
        )
    if is_fixed_identifier_line(source_text) and _same_fixed_identifier(
        source_text, translated_text
    ):
        return ConfidenceVerdict(is_low_confidence=False)
    preserved_title_or_identifier = _looks_like_preserved_title_or_identifier(
        source_text,
        translated_text,
        source_language=source_language,
        target_language=target_language,
    )

    ratio = len(translated_text) / max(len(source_text), 1)
    if not preserved_title_or_identifier and ratio < min_length_ratio:
        reasons.append(f"length ratio {ratio:.2f} < min {min_length_ratio:.2f}")
        tags.append(TAG_LENGTH_RATIO_ANOMALY)
    elif not preserved_title_or_identifier and ratio > max_length_ratio:
        reasons.append(f"length ratio {ratio:.2f} > max {max_length_ratio:.2f}")
        tags.append(TAG_LENGTH_RATIO_ANOMALY)

    source_punct = _count_punctuation(source_text)
    translated_punct = _count_punctuation(translated_text)
    delta = abs(translated_punct - source_punct)
    punctuation_limit = _punctuation_delta_limit(
        source_punct,
        max_punctuation_delta,
        source_language=source_language,
        target_language=target_language,
    )
    if not preserved_title_or_identifier and delta > punctuation_limit:
        reasons.append(
            f"punctuation delta {delta} > max {punctuation_limit}"
        )
        tags.append(TAG_PUNCTUATION_ANOMALY)

    if not preserved_title_or_identifier:
        truncation_reason = _detect_truncation(source_text, translated_text)
        if truncation_reason:
            reasons.append(truncation_reason)
            tags.append(TAG_TRUNCATED)

    residue_reason = None
    if not preserved_title_or_identifier:
        residue_reason = _source_language_residue(
            source_text,
            translated_text,
            source_language=source_language,
            target_language=target_language,
        )
    if residue_reason:
        reasons.append(residue_reason)
        tags.append(TAG_SOURCE_RESIDUE)
    english_leak_reason = _english_function_word_leak(
        source_text,
        translated_text,
        source_language=source_language,
        target_language=target_language,
    )
    if english_leak_reason and not preserved_title_or_identifier:
        reasons.append(english_leak_reason)
        tags.append(TAG_FUNCTION_WORD_RESIDUE)

    target_language_reason = _target_language_weak(
        source_text,
        translated_text,
        target_language=target_language,
    )
    if target_language_reason and not preserved_title_or_identifier:
        reasons.append(target_language_reason)
        tags.append(TAG_TARGET_LANGUAGE_WEAK)

    chatter_reason = _model_chatter(translated_text)
    if chatter_reason:
        reasons.append(chatter_reason)
        tags.append(TAG_MODEL_CHATTER)

    if not preserved_title_or_identifier and _too_similar(
        source_text, translated_text
    ):
        reasons.append("source and translation are too similar")
        tags.append(TAG_VERBATIM_ECHO)

    return ConfidenceVerdict(
        is_low_confidence=bool(reasons),
        reasons=tuple(reasons),
        tags=tuple(dict.fromkeys(tags)),
    )


def _count_punctuation(text: str) -> int:
    return sum(1 for char in text if char in _PUNCTUATION_CHARS)


def _punctuation_delta_limit(
    source_punctuation: int,
    configured_limit: int,
    *,
    source_language: Language | None,
    target_language: Language | None,
) -> int:
    if (
        source_language in _LATIN_SOURCE_LANGUAGES
        and target_language in _CHINESE_TARGET_LANGUAGES
    ):
        return max(configured_limit, round(source_punctuation * 0.75))
    return configured_limit


# Sentence-ending punctuation a completed translation should normally keep.
# These terminators are universal across CJK and Latin scripts, so the check
# works for any source/target language pair without per-language tables.
_TRUNCATION_SENTENCE_END = frozenset("。！？!?….")
# Closing quotes that may follow the sentence terminator.
_TRUNCATION_CLOSING_QUOTES = frozenset("”’』」〉》】》）»›")
_TRUNCATION_MIN_SOURCE_LENGTH = 6
_TRUNCATION_MIN_TARGET_LENGTH = 6
# Sentence-level truncation: when the source has at least this many
# sentence-ending marks but the translation has fewer than half, entire
# sentences were likely dropped. Require a minimum source length so that
# short exclamations with stacked punctuation (e.g. ``……!!``) don't
# inflate the count.
_TRUNCATION_MIN_SOURCE_SENTENCES = 3
_TRUNCATION_MIN_SOURCE_CHARS_FOR_SENTENCE_CHECK = 30
_TRUNCATION_SENTENCE_RATIO_THRESHOLD = 0.5


def _strip_trailing_closing_quotes(text: str) -> str:
    stripped = text.rstrip()
    while stripped and stripped[-1] in _TRUNCATION_CLOSING_QUOTES:
        stripped = stripped[:-1].rstrip()
    return stripped


def _count_sentence_enders(text: str) -> int:
    return sum(1 for char in text if char in _TRUNCATION_SENTENCE_END)


def _detect_truncation(source_text: str, translated_text: str) -> str | None:
    """Return a reason string when the translation appears truncated.

    Two complementary signals, both language-agnostic:

    1. **Word-level**: the source ends with a sentence terminator but the
       translation ends on a dangling word character (``str.isalnum`` covers
       CJK ideographs, kana, hangul, Latin letters, and digits across all
       scripts). The model cut mid-generation.

    2. **Sentence-level**: the source has multiple sentence-ending marks but
       the translation has fewer than half as many. The model stopped after
       translating only part of the source and added a period to make the
       output look complete. Length-ratio checks miss this because the ratio
       can stay barely above the minimum threshold.
    """

    source = _strip_trailing_closing_quotes(source_text.strip())
    if source and source[-1] in _TRUNCATION_SENTENCE_END:
        translated = _strip_trailing_closing_quotes(translated_text.strip())
        if (
            len(source) >= _TRUNCATION_MIN_SOURCE_LENGTH
            and len(translated) >= _TRUNCATION_MIN_TARGET_LENGTH
            and translated[-1] not in _TRUNCATION_SENTENCE_END
            and translated[-1].isalnum()
        ):
            return (
                "translation appears truncated; source ends a sentence "
                "but the translation ends mid-text"
            )

    if len(source) >= _TRUNCATION_MIN_SOURCE_CHARS_FOR_SENTENCE_CHECK:
        source_count = _count_sentence_enders(source_text)
        if source_count >= _TRUNCATION_MIN_SOURCE_SENTENCES:
            translated_count = _count_sentence_enders(translated_text)
            if (
                translated_count
                < source_count * _TRUNCATION_SENTENCE_RATIO_THRESHOLD
            ):
                return (
                    f"translation appears truncated; source has "
                    f"{source_count} sentence-ending marks but translation "
                    f"has only {translated_count}"
                )

    return None


# CJK ideographs (kanji / \u6f22\u5b57 / \u6c49\u5b57). Used as proof that the model
# did emit target-language content; presence of CJK in the output
# signals "the model tried" and gates how strict the residue checks
# get for legitimate emoji-fragment retention (\u314b\u314b\u314b / \u3160\u3160 alongside
# Chinese prose is fine; \u314b\u314b\u314b alone is not). Covers the basic block
# + Extension A + Compatibility Ideographs; skips Extension B (rare,
# surrogate-pair regex complexity).
_CJK_IDEOGRAPH_PATTERN = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
)


# Korean "hard" residue \u2014 these never appear legitimately in Chinese
# text; their presence always means the model failed to translate.
#   U+AC00-U+D7AF  Hangul Syllables (\uc548\ub155\ud558\uc138\uc694)
#   U+FFA0-U+FFDC  Halfwidth Hangul Jamo (legacy game-text leakage)
_KOREAN_HARD_RESIDUE_PATTERN = re.compile(r"[\uac00-\ud7af\uffa0-\uffdc]")

# Korean "soft" residue \u2014 Jamo blocks that may legitimately appear as
# emoji-fragment retention (\u314b\u314b\u314b / \u3160\u3160 in chat slang) when mixed
# with translated Chinese prose. Flag only when output is saturated
# AND has no CJK ideographs (= pure Korean = real laziness).
#   U+1100-U+11FF  Hangul Jamo
#   U+3130-U+318F  Hangul Compatibility Jamo
#   U+A960-U+A97F  Hangul Jamo Extended-A
#   U+D7B0-U+D7FF  Hangul Jamo Extended-B
_KOREAN_SOFT_RESIDUE_PATTERN = re.compile(
    r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\ud7b0-\ud7ff]"
)
_KOREAN_EMOTICON_JAMO = frozenset("\u314b\u314e\u3160\u315c")
_KOREAN_COMPATIBILITY_CONSONANT_PATTERN = re.compile(r"[\u3131-\u314e]")
_KOREAN_TARGET_SCRIPT_PATTERN = re.compile(
    r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f"
    r"\ua960-\ua97f\ud7b0-\ud7ff\uffa0-\uffdc]"
)
_KOREAN_SOURCE_SCRIPT_PATTERN = re.compile(
    r"[\uac00-\ud7af\uffa0-\uffdc\u1100-\u11ff\u3130-\u318f"
    r"\ua960-\ua97f\ud7b0-\ud7ff]"
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
_CHINESE_TARGET_LANGUAGES = {
    Language.CHINESE_SIMPLIFIED,
    Language.CHINESE_TRADITIONAL,
}
_LATIN_SOURCE_LANGUAGES = frozenset(
    {
        Language.ENGLISH,
        Language.FRENCH,
        Language.GERMAN,
        Language.HUNGARIAN,
        Language.INDONESIAN,
        Language.ITALIAN,
        Language.POLISH,
        Language.PORTUGUESE,
        Language.SPANISH,
        Language.TURKISH,
        Language.VIETNAMESE,
    }
)
_LATIN_LETTER_PATTERN = re.compile(r"[A-Za-z]")
_LATIN_SCRIPT_PATTERN = re.compile(
    r"[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff]"
)
_LATIN_WORD_PATTERN = re.compile(
    r"[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff]+"
    r"(?:['\u2019-][A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff]+)*"
)
_PROTECTED_LATIN_LITERAL_PATTERN = re.compile(
    r"(?:https?|ftp)://[^\s<>()]+|www\.[^\s<>()]+|"
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|"
    r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s<>()]*)?",
    re.I,
)
_CYRILLIC_SCRIPT_PATTERN = re.compile(
    r"[\u0400-\u052f\u1c80-\u1c8f\u2de0-\u2dff\ua640-\ua69f]"
)
_CYRILLIC_WORD_PATTERN = re.compile(
    r"[\u0400-\u052f\u1c80-\u1c8f\u2de0-\u2dff\ua640-\ua69f]+"
    r"(?:['\u2019-][\u0400-\u052f\u1c80-\u1c8f\u2de0-\u2dff\ua640-\ua69f]+)*"
)
_ARABIC_SCRIPT_PATTERN = re.compile(
    r"[\u0620-\u063f\u0641-\u064a\u066e-\u066f\u0671-\u06d3\u06d5"
    r"\u06e5-\u06e6\u06ee-\u06ef\u06fa-\u06fc\u06ff\u0750-\u077f"
    r"\u0870-\u0887\u08a0-\u08c9\ufb50-\ufdff\ufe70-\ufefc]"
)
_ARABIC_WORD_PATTERN = re.compile(
    r"[\u0620-\u063f\u0641-\u064a\u066e-\u066f\u0671-\u06d3\u06d5"
    r"\u06e5-\u06e6\u06ee-\u06ef\u06fa-\u06fc\u06ff\u0750-\u077f"
    r"\u0870-\u0887\u08a0-\u08c9\ufb50-\ufdff\ufe70-\ufefc]+"
)
_ASCII_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_ENGLISH_FUNCTION_WORDS = frozenset(
    """
    a about after all am an and are as at be been but by can could did do does
    for from had has have he her hers him his i if in into is it its me my not
    of on or our ours she should that the their theirs them they this those to
    under up us was we were with would you your yours
    """.split()
)
_ENGLISH_SINGLE_LEAK_WORDS = frozenset(
    """
    about after all am an and are as at be been but by can could did do does
    for from had has have he her hers him his if in into is it its me my not of
    on or our ours she should that the their theirs them they this those to
    under up us was we were with would you your yours
    """.split()
)
_ENGLISH_LEAK_MIN_FUNCTION_WORDS = 2
_ENGLISH_LEAK_MIN_LATIN_TOKENS = 3
_ENGLISH_LEAK_MIN_FUNCTION_RATIO = 0.40
_TARGET_SCRIPT_MIN_LENGTH = 20
_TARGET_SCRIPT_MIN_EXPECTED_RATIO = 0.10
_TARGET_SCRIPT_MIN_LATIN_RATIO = 0.40
_MODEL_CHATTER_PATTERNS = (
    re.compile(r"^\s*(?:translation|translated text|target text)\s*[:：]", re.I),
    re.compile(r"^\s*(?:译文|翻译|翻译结果|目标文本)\s*[:：]"),
    re.compile(r"^\s*(?:here is|here's)\b.{0,40}\btranslation\b", re.I),
    re.compile(r"^\s*(?:以下是|下面是).{0,16}(?:译文|翻译)"),
    re.compile(r"^\s*(?:as an ai|i(?:'|’)m sorry|sorry,?\s+i)\b", re.I),
    re.compile(r"```"),
)
_CLASSIC_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:ISBN(?:-1[03])?|ISSN|DOI|EAN|ASIN)\b", re.I
)
_LATIN_NUMBER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[xX][A-Za-z0-9]+)?")
_NUMBERED_LATIN_NOTATION_PREFIX_PATTERN = re.compile(
    r"^[^\w\s]{0,3}\s*\d{1,4}\s*[.)\-:：、]\s*"
)
_TITLE_CONNECTOR_WORDS = frozenset(
    "a an and at by for from in of on or the to vs with".split()
)
_PRESERVED_TITLE_MAX_SOURCE_CHARS = 120
_PRESERVED_TITLE_MAX_TARGET_CHARS = 160
_PRESERVED_TITLE_MAX_LATIN_TOKENS = 6
_PRESERVED_TITLE_MAX_SOURCE_SCRIPT_RATIO = 0.25
_PRESERVED_TITLE_MIN_TARGET_TOKEN_COVERAGE = 0.60
_SYMBOLIC_JAMO_MAX_CHARS = 4
_SCRIPT_RESIDUE_MIN_PURE_CHARS = 12
_SCRIPT_RESIDUE_MIN_PURE_LETTERS = 8
_SCRIPT_RESIDUE_MIN_PURE_RATIO = 0.35
_SCRIPT_RESIDUE_MAX_TARGET_RATIO = 0.10


@dataclass(frozen=True)
class _ScriptResiduePolicy:
    name: str
    character_pattern: re.Pattern[str]
    word_pattern: re.Pattern[str]
    shared_word_count: int
    shared_character_count: int
    single_lowercase_word_min_chars: int | None = None


_LATIN_RESIDUE_POLICY = _ScriptResiduePolicy(
    name="Latin-script",
    character_pattern=_LATIN_SCRIPT_PATTERN,
    word_pattern=_LATIN_WORD_PATTERN,
    shared_word_count=4,
    shared_character_count=20,
    single_lowercase_word_min_chars=8,
)
_SOURCE_SCRIPT_POLICIES: dict[Language, _ScriptResiduePolicy] = {
    **{language: _LATIN_RESIDUE_POLICY for language in _LATIN_SOURCE_LANGUAGES},
    Language.RUSSIAN: _ScriptResiduePolicy(
        name="Cyrillic",
        character_pattern=_CYRILLIC_SCRIPT_PATTERN,
        word_pattern=_CYRILLIC_WORD_PATTERN,
        shared_word_count=3,
        shared_character_count=18,
    ),
    Language.ARABIC: _ScriptResiduePolicy(
        name="Arabic",
        character_pattern=_ARABIC_SCRIPT_PATTERN,
        word_pattern=_ARABIC_WORD_PATTERN,
        shared_word_count=3,
        shared_character_count=18,
    ),
}


def _looks_like_preserved_title_or_identifier(
    source_text: str,
    translated_text: str,
    *,
    source_language: Language | None,
    target_language: Language | None,
) -> bool:
    """Allow short title/identifier lines to keep Latin tokens in output."""

    source = source_text.strip()
    translated = translated_text.strip()
    if not source or not translated:
        return False
    if _looks_like_exact_symbolic_jamo(
        source,
        translated,
        source_language=source_language,
    ):
        return True
    if (
        source_language in _LATIN_SOURCE_LANGUAGES
        and target_language in _CHINESE_TARGET_LANGUAGES
    ):
        if _CLASSIC_IDENTIFIER_PATTERN.search(source):
            return True
        if (
            len(source) <= _PRESERVED_TITLE_MAX_SOURCE_CHARS
            and len(translated) <= _PRESERVED_TITLE_MAX_TARGET_CHARS
            and _looks_like_short_latin_title_text(source)
            and _looks_like_short_latin_title_text(translated)
            and (
                _CJK_IDEOGRAPH_PATTERN.search(translated) is not None
                or (
                    _normalize_for_similarity(source)
                    == _normalize_for_similarity(translated)
                    and _looks_like_latin_product_identifier(source)
                )
            )
        ):
            return True
        return False
    if _source_language_residue(
        source,
        translated,
        source_language=source_language,
        target_language=target_language,
    ):
        return False
    if (
        _normalize_for_similarity(source) == _normalize_for_similarity(translated)
        and _looks_like_numbered_latin_notation_title(source)
    ):
        return True
    if _CLASSIC_IDENTIFIER_PATTERN.search(source) or _CLASSIC_IDENTIFIER_PATTERN.search(
        translated
    ):
        return True
    if (
        len(source) > _PRESERVED_TITLE_MAX_SOURCE_CHARS
        or len(translated) > _PRESERVED_TITLE_MAX_TARGET_CHARS
    ):
        return False
    if (
        _source_script_ratio(source, source_language)
        > _PRESERVED_TITLE_MAX_SOURCE_SCRIPT_RATIO
    ):
        return False
    source_tokens = set(_latin_number_tokens(source))
    translated_tokens = set(_latin_number_tokens(translated))
    if not source_tokens or not translated_tokens:
        return False
    shared = source_tokens & translated_tokens
    if not shared:
        return False
    target_coverage = len(shared) / len(translated_tokens)
    if target_coverage < _PRESERVED_TITLE_MIN_TARGET_TOKEN_COVERAGE:
        return False
    return _CJK_IDEOGRAPH_PATTERN.search(translated) is not None or (
        _looks_like_short_latin_title_text(source)
        and _looks_like_short_latin_title_text(translated)
    )


def _looks_like_short_latin_title_text(text: str) -> bool:
    tokens = [
        match.group(0) for match in _LATIN_NUMBER_TOKEN_PATTERN.finditer(text)
    ]
    if not tokens or len(tokens) > _PRESERVED_TITLE_MAX_LATIN_TOKENS:
        return False
    has_title_token = False
    for token in tokens:
        folded = token.casefold()
        if any(char.isdigit() for char in token):
            has_title_token = True
            continue
        if folded in _TITLE_CONNECTOR_WORDS:
            continue
        if (
            token.isupper()
            or token[:1].isupper()
            or any(char.isupper() for char in token[1:])
        ):
            has_title_token = True
            continue
        if not token[:1].isalpha():
            return False
        return False
    return has_title_token


def _looks_like_numbered_latin_notation_title(text: str) -> bool:
    """Recognize concise numbered titles such as musical movement labels.

    A declared Korean or Japanese source can contain a short Latin heading that
    is intentionally unchanged. Requiring a numbered marker, a title-like token,
    and at most one ordinary lowercase word keeps prose echoes out of this path.
    """

    if not _NUMBERED_LATIN_NOTATION_PREFIX_PATTERN.match(text):
        return False
    tokens = [
        match.group(0) for match in _LATIN_NUMBER_TOKEN_PATTERN.finditer(text)
    ]
    if not tokens or len(tokens) > _PRESERVED_TITLE_MAX_LATIN_TOKENS:
        return False
    has_title_token = False
    lowercase_words: list[str] = []
    for token in tokens:
        if any(char.isdigit() for char in token):
            continue
        folded = token.casefold()
        if folded in _TITLE_CONNECTOR_WORDS:
            continue
        if (
            token.isupper()
            or token[:1].isupper()
            or any(char.isupper() for char in token[1:])
        ):
            has_title_token = True
            continue
        lowercase_words.append(token)
    long_lowercase_words = [token for token in lowercase_words if len(token) > 2]
    short_notation_tokens = [token for token in lowercase_words if len(token) <= 2]
    return (
        has_title_token
        and len(long_lowercase_words) <= 1
        and len(short_notation_tokens) <= 2
    )


def _looks_like_exact_symbolic_jamo(
    source: str,
    translated: str,
    *,
    source_language: Language | None,
) -> bool:
    """Allow a single compatibility Jamo framed as a compact emoticon."""

    if source_language is not Language.KOREAN or source != translated:
        return False
    compact = "".join(source.split())
    if not compact or len(compact) > _SYMBOLIC_JAMO_MAX_CHARS:
        return False
    soft_jamo = _KOREAN_SOFT_RESIDUE_PATTERN.findall(compact)
    if len(soft_jamo) != 1 or not _KOREAN_COMPATIBILITY_CONSONANT_PATTERN.fullmatch(
        soft_jamo[0]
    ):
        return False
    return any(not char.isalnum() for char in compact)


def _looks_like_latin_product_identifier(text: str) -> bool:
    tokens = [
        match.group(0) for match in _LATIN_NUMBER_TOKEN_PATTERN.finditer(text)
    ]
    return bool(tokens) and (
        any(char.isdigit() for token in tokens for char in token)
        or any(
            any(char.isupper() for char in token[1:]) and not token.isupper()
            for token in tokens
        )
    )


def _source_script_ratio(text: str, source_language: Language | None) -> float:
    if source_language is Language.KOREAN:
        return _ratio(_KOREAN_SOURCE_SCRIPT_PATTERN, text)
    if source_language is Language.JAPANESE:
        return _ratio(_JAPANESE_KANA_PATTERN, text)
    policy = _SOURCE_SCRIPT_POLICIES.get(source_language)
    if policy is not None:
        return _ratio(policy.character_pattern, text)
    return 0.0


def _latin_number_tokens(text: str) -> list[str]:
    return [
        match.group(0).casefold()
        for match in _LATIN_NUMBER_TOKEN_PATTERN.finditer(text)
    ]


def _source_language_residue(
    source_text: str,
    translated_text: str,
    *,
    source_language: Language | None,
    target_language: Language | None,
) -> str | None:
    if source_language is Language.KOREAN:
        # Hard residue (real Korean words / halfwidth legacy) is always
        # a problem.
        if _KOREAN_HARD_RESIDUE_PATTERN.search(translated_text):
            return "Korean residue remains in translation"
        # Mixed Chinese output may retain explicit chat emoticons or one
        # compatibility consonant as a cultural marker. Other Jamo embedded
        # in Chinese usually indicates a partially decoded Korean syllable.
        has_cjk = bool(_CJK_IDEOGRAPH_PATTERN.search(translated_text))
        soft_jamo = _KOREAN_SOFT_RESIDUE_PATTERN.findall(translated_text)
        if soft_jamo and (
            (
                not has_cjk
                and _ratio(_KOREAN_SOFT_RESIDUE_PATTERN, translated_text)
                > _JAMO_RATIO_THRESHOLD
            )
            or (has_cjk and not _allows_mixed_jamo_retention(soft_jamo))
        ):
            return "Korean residue remains in translation"
    elif source_language is Language.JAPANESE:
        if _JAPANESE_KANA_PATTERN.search(translated_text):
            return "Japanese kana residue remains in translation"
    elif target_language in _CHINESE_TARGET_LANGUAGES:
        policy = _SOURCE_SCRIPT_POLICIES.get(source_language)
        if policy is not None and _has_script_residue(
            source_text,
            translated_text,
            policy,
        ):
            return f"{policy.name} source residue remains in Chinese translation"
    return None


def _has_script_residue(
    source_text: str,
    translated_text: str,
    policy: _ScriptResiduePolicy,
) -> bool:
    text = translated_text.strip()
    if len(text) >= _SCRIPT_RESIDUE_MIN_PURE_CHARS:
        script_letters = len(policy.character_pattern.findall(text))
        if (
            script_letters >= _SCRIPT_RESIDUE_MIN_PURE_LETTERS
            and script_letters / len(text) >= _SCRIPT_RESIDUE_MIN_PURE_RATIO
            and _ratio(_CJK_IDEOGRAPH_PATTERN, text)
            < _SCRIPT_RESIDUE_MAX_TARGET_RATIO
        ):
            return True

    source_tokens = _script_tokens(source_text, policy.word_pattern)
    translated_tokens = _script_tokens(translated_text, policy.word_pattern)
    if (
        policy.single_lowercase_word_min_chars is not None
        and _CJK_IDEOGRAPH_PATTERN.search(text)
        and _has_shared_lowercase_content_word(
            source_tokens,
            text,
            policy.word_pattern,
            policy.single_lowercase_word_min_chars,
        )
    ):
        return True
    size = policy.shared_word_count
    if len(source_tokens) < size or len(translated_tokens) < size:
        return False
    source_windows = {
        tuple(source_tokens[index : index + size])
        for index in range(len(source_tokens) - size + 1)
    }
    for index in range(len(translated_tokens) - size + 1):
        window = tuple(translated_tokens[index : index + size])
        if (
            window in source_windows
            and sum(len(token) for token in window)
            >= policy.shared_character_count
        ):
            return True
    return False


def _has_shared_lowercase_content_word(
    source_tokens: list[str],
    translated_text: str,
    word_pattern: re.Pattern[str],
    min_chars: int,
) -> bool:
    source_token_set = set(source_tokens)
    normalized = unicodedata.normalize("NFC", translated_text)
    protected_spans = [
        match.span() for match in _PROTECTED_LATIN_LITERAL_PATTERN.finditer(normalized)
    ]
    for match in word_pattern.finditer(normalized):
        token = match.group(0)
        if len(token) < min_chars or not token.islower():
            continue
        if token.casefold() not in source_token_set:
            continue
        if any(
            match.start() < end and match.end() > start
            for start, end in protected_spans
        ):
            continue
        return True
    return False


def _script_tokens(text: str, pattern: re.Pattern[str]) -> list[str]:
    normalized = unicodedata.normalize("NFC", text)
    return [
        match.group(0).casefold()
        for match in pattern.finditer(normalized)
    ]


def _allows_mixed_jamo_retention(soft_jamo: list[str]) -> bool:
    if all(char in _KOREAN_EMOTICON_JAMO for char in soft_jamo):
        return True
    return len(soft_jamo) == 1 and bool(
        _KOREAN_COMPATIBILITY_CONSONANT_PATTERN.fullmatch(soft_jamo[0])
    )


def _english_function_word_leak(
    source_text: str,
    translated_text: str,
    *,
    source_language: Language | None,
    target_language: Language | None,
) -> str | None:
    if source_language is Language.ENGLISH:
        return None
    if target_language not in _CHINESE_TARGET_LANGUAGES:
        return None
    words = [
        match.group(0).casefold()
        for match in _ASCII_WORD_PATTERN.finditer(translated_text)
    ]
    # Translation prompts explicitly preserve Latin names, abbreviations, and
    # role markers such as A/B/C. Remove only the occurrences already present
    # in the source before looking for newly introduced English prose.
    source_counts = Counter(
        match.group(0).casefold()
        for match in _ASCII_WORD_PATTERN.finditer(source_text)
    )
    introduced_words: list[str] = []
    for word in words:
        if source_counts[word] > 0:
            source_counts[word] -= 1
        else:
            introduced_words.append(word)
    words = introduced_words
    if _CJK_IDEOGRAPH_PATTERN.search(translated_text) and any(
        word in _ENGLISH_SINGLE_LEAK_WORDS for word in words
    ):
        return "English function-word residue remains in Chinese translation"
    if len(words) < _ENGLISH_LEAK_MIN_LATIN_TOKENS:
        return None
    function_count = sum(1 for word in words if word in _ENGLISH_FUNCTION_WORDS)
    if function_count < _ENGLISH_LEAK_MIN_FUNCTION_WORDS:
        return None
    function_ratio = function_count / len(words)
    if function_ratio < _ENGLISH_LEAK_MIN_FUNCTION_RATIO:
        return None
    return "English function-word residue remains in Chinese translation"


def _target_language_weak(
    source_text: str,
    translated_text: str,
    *,
    target_language: Language | None,
) -> str | None:
    text = translated_text.strip()
    if len(text) < _TARGET_SCRIPT_MIN_LENGTH:
        return None
    if is_fixed_identifier_line(source_text) and _same_fixed_identifier(
        source_text, translated_text
    ):
        return None
    latin_ratio = _ratio(_LATIN_LETTER_PATTERN, text)
    if latin_ratio < _TARGET_SCRIPT_MIN_LATIN_RATIO:
        return None
    if target_language in _CHINESE_TARGET_LANGUAGES:
        expected_ratio = _ratio(_CJK_IDEOGRAPH_PATTERN, text)
        if expected_ratio < _TARGET_SCRIPT_MIN_EXPECTED_RATIO:
            return "target-language script is weak for Chinese translation"
    elif target_language is Language.KOREAN:
        expected_ratio = _ratio(_KOREAN_TARGET_SCRIPT_PATTERN, text)
        if expected_ratio < _TARGET_SCRIPT_MIN_EXPECTED_RATIO:
            return "target-language script is weak for Korean translation"
    elif target_language is Language.JAPANESE:
        expected_ratio = _ratio(_JAPANESE_KANA_PATTERN, text) + _ratio(
            _CJK_IDEOGRAPH_PATTERN, text
        )
        if expected_ratio < _TARGET_SCRIPT_MIN_EXPECTED_RATIO:
            return "target-language script is weak for Japanese translation"
    return None


def _model_chatter(translated_text: str) -> str | None:
    if any(pattern.search(translated_text) for pattern in _MODEL_CHATTER_PATTERNS):
        return "model chatter or wrapper text remains in translation"
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


def _same_fixed_identifier(source_text: str, translated_text: str) -> bool:
    compact_source = re.sub(r"[-\s:：|｜]", "", source_text).casefold()
    compact_translated = re.sub(r"[-\s:：|｜]", "", translated_text).casefold()
    return compact_source == compact_translated


__all__ = ["ConfidenceVerdict", "evaluate_segment_confidence"]
