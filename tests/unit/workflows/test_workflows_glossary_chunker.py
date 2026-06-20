from __future__ import annotations

from pathlib import Path

import pytest

from transoria.domain import Language
from transoria.workflows.glossary import build_glossary_chunks


def test_chunker_groups_segments_within_char_limit() -> None:
    segments = (
        "first line",
        "second line",
        "third line is longer than the others",
    )

    chunks = build_glossary_chunks(
        {Path("/a.txt"): segments}, chunk_char_limit=25
    )

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text  # non-empty


def test_chunker_emits_one_chunk_per_file_when_under_limit() -> None:
    chunks = build_glossary_chunks(
        {
            Path("/a.txt"): ("hello",),
            Path("/b.txt"): ("world",),
        },
        chunk_char_limit=200,
    )

    assert [chunk.source_file for chunk in chunks] == [Path("/a.txt"), Path("/b.txt")]
    assert chunks[0].text == "hello"
    assert chunks[1].text == "world"


def test_chunker_attributes_chunk_to_originating_file() -> None:
    chunks = build_glossary_chunks(
        {
            Path("/a.txt"): tuple(f"line {n}" for n in range(20)),
            Path("/b.txt"): tuple(f"other {n}" for n in range(20)),
        },
        chunk_char_limit=30,
    )

    files = {chunk.source_file for chunk in chunks}
    assert files == {Path("/a.txt"), Path("/b.txt")}


def test_chunker_splits_oversized_segment() -> None:
    long_line = "x" * 500

    chunks = build_glossary_chunks(
        {Path("/a.txt"): (long_line,)}, chunk_char_limit=50
    )

    assert len(chunks) == 10
    assert "".join(chunk.text for chunk in chunks) == long_line
    assert all(len(chunk.text) <= 50 for chunk in chunks)


def test_chunker_splits_oversized_segment_on_sentence_boundaries() -> None:
    text = "第一句有角色A。第二句有角色B！第三句有角色C？第四句有角色D。"

    chunks = build_glossary_chunks(
        {Path("/a.txt"): (text,)}, chunk_char_limit=18
    )

    assert [chunk.text for chunk in chunks] == [
        "第一句有角色A。第二句有角色B！",
        "第三句有角色C？第四句有角色D。",
    ]


def test_chunker_flushes_buffer_before_oversized_segment_pieces() -> None:
    chunks = build_glossary_chunks(
        {Path("/a.txt"): ("short", "x" * 30)}, chunk_char_limit=10
    )

    assert [chunk.text for chunk in chunks] == [
        "short",
        "x" * 10,
        "x" * 10,
        "x" * 10,
    ]


def test_chunker_rejects_zero_or_negative_limit() -> None:
    with pytest.raises(ValueError):
        build_glossary_chunks({Path("/a.txt"): ("x",)}, chunk_char_limit=0)


def test_chunker_skips_empty_segments() -> None:
    chunks = build_glossary_chunks(
        {Path("/a.txt"): ("hello", "", "world")}, chunk_char_limit=200
    )

    assert chunks[0].text == "hello\nworld"


def test_chunker_skips_pure_numeric_and_punctuation_segments() -> None:
    # Page numbers, decorative dividers, pure ellipses carry no
    # extractable proper nouns; the chunker drops them so they don't
    # waste tokens.
    chunks = build_glossary_chunks(
        {
            Path("/a.txt"): (
                "Chapter 1",
                "1234",
                "———",
                "「他低声说道」",
                "...",
                "(42)",
                "second sentence here",
            )
        },
        chunk_char_limit=500,
    )

    assert len(chunks) == 1
    # Note: 「他低声说道」 is pure CJK quotation marks framing letters —
    # the letters keep it. Pure-punctuation / pure-numeric lines are gone.
    assert chunks[0].text == "Chapter 1\n「他低声说道」\nsecond sentence here"


def test_chunker_cleans_common_ruby_annotations_before_extraction() -> None:
    chunks = build_glossary_chunks(
        {
            Path("/a.txt"): (
                "漢字(かんじ) and 이름(이름)",
                "[ruby text=かんじ]漢字[/ruby]",
            )
        },
        chunk_char_limit=200,
    )

    assert chunks[0].text == "漢字 and 이름\n漢字"


def test_chunker_skips_segments_without_configured_source_language_script() -> None:
    chunks = build_glossary_chunks(
        {
            Path("/novel.txt"): (
                "これは日本語の本文です。",
                "반대쪽도 걸어 도망가지 못하게 한다.",
                "这是已经翻好的中文。",
            )
        },
        chunk_char_limit=500,
        source_language=Language.KOREAN,
    )

    assert len(chunks) == 1
    assert chunks[0].text == "반대쪽도 걸어 도망가지 못하게 한다."


def test_chunker_applies_source_language_filter_to_japanese_sources() -> None:
    chunks = build_glossary_chunks(
        {
            Path("/novel.txt"): (
                "これは日本語の本文です。",
                "반대쪽도 걸어 도망가지 못하게 한다.",
            )
        },
        chunk_char_limit=500,
        source_language=Language.JAPANESE,
    )

    assert len(chunks) == 1
    assert chunks[0].text == "これは日本語の本文です。"
