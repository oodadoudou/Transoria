from __future__ import annotations

from pathlib import Path

import pytest

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


def test_chunker_keeps_oversized_segment_intact() -> None:
    long_line = "x" * 500

    chunks = build_glossary_chunks(
        {Path("/a.txt"): (long_line,)}, chunk_char_limit=50
    )

    assert len(chunks) == 1
    assert chunks[0].text == long_line


def test_chunker_rejects_zero_or_negative_limit() -> None:
    with pytest.raises(ValueError):
        build_glossary_chunks({Path("/a.txt"): ("x",)}, chunk_char_limit=0)


def test_chunker_skips_empty_segments() -> None:
    chunks = build_glossary_chunks(
        {Path("/a.txt"): ("hello", "", "world")}, chunk_char_limit=200
    )

    assert chunks[0].text == "hello\nworld"


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
