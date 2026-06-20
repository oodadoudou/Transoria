"""Tests for ``transoria.workflows.prefilter``."""

from __future__ import annotations

import pytest

from transoria.domain import Language
from transoria.workflows.prefilter import contains_source_language_script
from transoria.workflows.prefilter import is_translation_skippable
from transoria.workflows.prefilter import should_translate_for_language


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "\t",
        "\n\n",
        "　",  # full-width space
    ],
)
def test_skippable_blank(text: str) -> None:
    assert is_translation_skippable(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "1",
        "1234",
        "0.5",
        "1/10",
        "(1)",
        "12-34",
        "5%",
        "  42  ",
        "ISBN | 979-11-01-87478-2",
        "ISBN-13: 978 1 4028 9462 6",
        "ISBN 0-306-40615-2",
    ],
)
def test_skippable_numeric(text: str) -> None:
    assert is_translation_skippable(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "...",
        "……",
        "———",
        "***",
        "・・・",
        "「」",
        "『』",
        "！？",
        ". , ! ?",
        "—",
    ],
)
def test_skippable_pure_punctuation(text: str) -> None:
    assert is_translation_skippable(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "🎉",
        "♠♣♥♦",
    ],
)
def test_skippable_pure_symbols(text: str) -> None:
    assert is_translation_skippable(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Hello",
        "Chapter 5",
        "VII",  # Roman numerals — Latin letters
        "안녕",  # Korean
        "你好",  # Chinese
        "こんにちは",  # Japanese
        "第5章",  # CJK + digit
        "5월",  # digit + Korean
        "1. 第一章",
        "a",
        " A. ",
    ],
)
def test_kept_when_letters_present(text: str) -> None:
    assert is_translation_skippable(text) is False


def test_kept_when_letters_mixed_with_punctuation_and_digits() -> None:
    # Sentences are obviously kept regardless of trailing punctuation.
    assert is_translation_skippable("It cost $1,234.56!") is False
    assert is_translation_skippable("그는 말했다: ‘1234’.") is False


def test_language_prefilter_skips_already_translated_chinese_for_korean_source() -> None:
    assert (
        should_translate_for_language(
            "这是已经补过的中文段落。",
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
        )
        is False
    )


def test_language_prefilter_keeps_korean_residue_inside_chinese_text() -> None:
    assert (
        should_translate_for_language(
            "前面是中文，但这里还有 반대쪽도 걸어 도망가지 못하게 한다.",
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
        )
        is True
    )


def test_language_prefilter_keeps_neutral_titles_without_target_script() -> None:
    assert (
        should_translate_for_language(
            "Special Chapter",
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
        )
        is True
    )


def test_language_prefilter_is_conservative_for_japanese_to_chinese() -> None:
    assert (
        should_translate_for_language(
            "東京",
            source_language=Language.JAPANESE,
            target_language=Language.CHINESE_SIMPLIFIED,
        )
        is True
    )


def test_language_prefilter_covers_every_supported_language() -> None:
    for language in Language:
        should_translate_for_language(
            "already translated",
            source_language=language,
            target_language=Language.CHINESE_SIMPLIFIED,
        )


def test_source_language_script_filter_skips_japanese_for_korean_source() -> None:
    assert contains_source_language_script("これは日本語の本文です。", Language.KOREAN) is False


def test_source_language_script_filter_keeps_korean_residue_for_korean_source() -> None:
    assert (
        contains_source_language_script(
            "前面是中文，但这里还有 반대쪽도 걸어 도망가지 못하게 한다.",
            Language.KOREAN,
        )
        is True
    )


def test_source_language_script_filter_keeps_japanese_kanji_for_japanese_source() -> None:
    assert contains_source_language_script("東京", Language.JAPANESE) is True


def test_source_language_script_filter_covers_every_supported_language() -> None:
    for language in Language:
        contains_source_language_script("already translated", language)
