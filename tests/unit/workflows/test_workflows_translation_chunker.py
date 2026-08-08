from __future__ import annotations

import json

import pytest

from transoria.workflows.translation import (
    Glossary,
    GlossaryEntry,
    PreparedSegment,
    PreprocessedSegment,
    ProtectionMap,
    assemble_user_prompt,
    build_chunks,
    format_context_section,
    format_glossary_section,
)


def _prep(segment_id: str, text: str) -> PreparedSegment:
    return PreparedSegment(
        segment_id=segment_id,
        original_text=text,
        preprocessed=PreprocessedSegment(
            prompt_text=text,
            protection=ProtectionMap(spans=()),
        ),
    )


def test_build_chunks_splits_by_size_and_indexes_locally() -> None:
    items = tuple(_prep(f"0:{i}", f"line {i}") for i in range(7))

    chunks = build_chunks(
        items,
        chunk_size=3,
        context_line_count=0,
        glossary=Glossary.empty(),
    )

    assert tuple(len(chunk.segments) for chunk in chunks) == (3, 3, 1)
    assert tuple(seg.chunk_index for seg in chunks[1].segments) == (0, 1, 2)
    assert tuple(seg.segment_id for seg in chunks[1].segments) == ("0:3", "0:4", "0:5")


def test_build_chunks_splits_by_token_limit_when_counter_is_supplied() -> None:
    items = (
        _prep("0:0", "one two"),
        _prep("0:1", "three four"),
        _prep("0:2", "five"),
    )

    chunks = build_chunks(
        items,
        chunk_size=10,
        chunk_token_limit=3,
        token_counter=lambda text: len(text.split()),
        context_line_count=0,
        glossary=Glossary.empty(),
    )

    assert tuple(tuple(seg.segment_id for seg in chunk.segments) for chunk in chunks) == (
        ("0:0",),
        ("0:1", "0:2"),
    )


def test_build_chunks_dynamic_budget_counts_the_assembled_user_prompt() -> None:
    items = (_prep("0:0", "first"), _prep("0:1", "second"))
    single = build_chunks(
        items[:1],
        chunk_size=10,
        context_line_count=0,
        glossary=Glossary.empty(),
    )[0]

    chunks = build_chunks(
        items,
        chunk_size=10,
        dynamic_input_token_limit=len(assemble_user_prompt(single)),
        dynamic_input_token_counter=len,
        context_line_count=0,
        glossary=Glossary.empty(),
    )

    assert tuple(tuple(seg.segment_id for seg in chunk.segments) for chunk in chunks) == (
        ("0:0",),
        ("0:1",),
    )


def test_build_chunks_dynamic_budget_drops_oldest_context_first() -> None:
    items = (
        _prep("0:0", "Far context."),
        _prep("0:1", "Near context."),
        _prep("0:2", "Translate this."),
    )

    def count_prompt(text: str) -> int:
        return 2 if "Far context." in text and "Translate this." in text else 1

    chunks = build_chunks(
        items,
        chunk_size=1,
        dynamic_input_token_limit=1,
        dynamic_input_token_counter=count_prompt,
        context_line_count=2,
        glossary=Glossary.empty(),
    )

    assert chunks[2].context_lines == ("Near context.",)


def test_build_chunks_dynamic_budget_keeps_oversized_segment_and_glossary() -> None:
    items = (_prep("0:0", "신해범 entered a very long passage"),)
    glossary = Glossary(entries=(GlossaryEntry(src="신해범", dst="申海范"),))

    chunks = build_chunks(
        items,
        chunk_size=10,
        dynamic_input_token_limit=1,
        dynamic_input_token_counter=lambda text: len(text),
        context_line_count=0,
        glossary=glossary,
    )

    assert tuple(segment.prompt_text for segment in chunks[0].segments) == (
        "신해범 entered a very long passage",
    )
    assert tuple(entry.src for entry in chunks[0].glossary_entries) == ("신해범",)


def test_build_chunks_never_crosses_file_boundaries() -> None:
    items = (
        _prep("0:0", "file zero"),
        _prep("1:0", "file one"),
    )

    chunks = build_chunks(
        items,
        chunk_size=10,
        chunk_token_limit=100,
        token_counter=lambda text: 1,
        context_line_count=0,
        glossary=Glossary.empty(),
    )

    assert tuple(tuple(seg.segment_id for seg in chunk.segments) for chunk in chunks) == (
        ("0:0",),
        ("1:0",),
    )


def test_build_chunks_attaches_context_window() -> None:
    items = tuple(_prep(f"0:{i}", f"line {i}.") for i in range(6))

    chunks = build_chunks(
        items,
        chunk_size=2,
        context_line_count=2,
        glossary=Glossary.empty(),
    )

    assert chunks[0].context_lines == ()
    assert chunks[1].context_lines == ("line 0.", "line 1.")
    assert chunks[2].context_lines == ("line 2.", "line 3.")


def test_build_chunks_context_window_stops_at_non_punctuation_line() -> None:
    # Non-punctuation-ending lines break the preceding-context chain so
    # the model only sees a clean sentence-bounded run of preceding text,
    # which keeps token cost bounded even when the threshold is large.
    items = (
        _prep("0:0", "Earlier earlier sentence."),
        _prep("0:1", "Section header without punctuation"),
        _prep("0:2", "Sentence right before chunk."),
        _prep("0:3", "First chunk line."),
    )

    chunks = build_chunks(
        items,
        chunk_size=1,
        context_line_count=10,
        glossary=Glossary.empty(),
    )

    # Chunk that starts at index 3: walking back, "Sentence right before
    # chunk." is included; "Section header without punctuation" breaks
    # the chain so "Earlier earlier sentence." is NOT reached.
    assert chunks[3].context_lines == ("Sentence right before chunk.",)


def test_build_chunks_context_window_skips_blank_lines_without_breaking() -> None:
    # Empty paragraphs / blank lines are skipped without ending the
    # preceding-context chain, mirroring novel-style paragraph breaks.
    items = (
        _prep("0:0", "First sentence."),
        _prep("0:1", ""),
        _prep("0:2", "Second sentence."),
        _prep("0:3", "Chunk start line."),
    )

    chunks = build_chunks(
        items,
        chunk_size=1,
        context_line_count=10,
        glossary=Glossary.empty(),
    )

    assert chunks[3].context_lines == ("First sentence.", "Second sentence.")


def test_build_chunks_context_window_respects_cjk_punctuation() -> None:
    items = (
        _prep("0:0", "上一段。"),
        _prep("0:1", "中段……"),
        _prep("0:2", "「他说道」"),
        _prep("0:3", "本块第一行。"),
    )

    chunks = build_chunks(
        items,
        chunk_size=1,
        context_line_count=5,
        glossary=Glossary.empty(),
    )

    assert chunks[3].context_lines == ("上一段。", "中段……", "「他说道」")


def test_build_chunks_attaches_only_matched_glossary_entries() -> None:
    items = (_prep("0:0", "신해범 entered"), _prep("0:1", "regular line"))
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="신해범", dst="申海范"),
            GlossaryEntry(src="흑룡", dst="黑龙"),
        )
    )

    chunks = build_chunks(
        items, chunk_size=4, context_line_count=0, glossary=glossary
    )

    assert tuple(entry.src for entry in chunks[0].glossary_entries) == ("신해범",)


def test_build_chunks_rejects_zero_chunk_size() -> None:
    with pytest.raises(ValueError):
        build_chunks(
            (_prep("0:0", "x"),),
            chunk_size=0,
            context_line_count=0,
            glossary=Glossary.empty(),
        )


def test_jsonl_input_is_one_object_per_line_with_chunk_indices() -> None:
    items = (_prep("0:0", "first"), _prep("0:1", "second"))

    chunks = build_chunks(
        items, chunk_size=4, context_line_count=0, glossary=Glossary.empty()
    )

    parsed = [json.loads(line) for line in chunks[0].jsonl_input().splitlines()]
    assert parsed == [{"0": "first"}, {"1": "second"}]


def test_format_glossary_section_renders_each_entry_on_its_own_line() -> None:
    entries = (
        GlossaryEntry(src="신해범", dst="申海范", info="Male Name"),
        GlossaryEntry(src="공이", dst="孔二"),
    )

    section = format_glossary_section(entries)

    assert "신해범 -> 申海范 (Male Name)" in section
    assert "공이 -> 孔二" in section


def test_format_context_section_emits_plain_text_lines() -> None:
    # The model only reads the context for narrative continuity; it
    # never needs to parse it back to indices, so plain-text
    # one-per-line is the cheapest reliable shape.
    section = format_context_section(("first", "second", "third"))

    assert section.splitlines() == ["first", "second", "third"]


def test_format_context_section_strips_embedded_newlines_and_blanks() -> None:
    # Source segments may contain stray newlines (EPUB spans). We flatten
    # them so each context entry stays on its own line and stays
    # readable to the model. Empty entries are dropped.
    section = format_context_section(("a\nb", "", "c "))

    assert section.splitlines() == ["a b", "c"]


def test_assemble_user_prompt_orders_glossary_then_context_then_translate() -> None:
    items = (_prep("0:0", "신해범 walks"),)
    glossary = Glossary(entries=(GlossaryEntry(src="신해범", dst="申海范"),))
    chunks = build_chunks(
        items, chunk_size=4, context_line_count=0, glossary=glossary
    )

    rendered = assemble_user_prompt(chunks[0])

    glossary_pos = rendered.index("[Glossary]")
    translate_pos = rendered.index("[Translate]")
    assert glossary_pos < translate_pos
    assert "신해범 -> 申海范" in rendered
    assert '{"0": "신해범 walks"}' in rendered


def test_assemble_user_prompt_omits_empty_sections() -> None:
    items = (_prep("0:0", "plain"),)
    chunks = build_chunks(
        items, chunk_size=4, context_line_count=0, glossary=Glossary.empty()
    )

    rendered = assemble_user_prompt(chunks[0])

    assert "[Glossary]" not in rendered
    assert "[Context]" not in rendered
    assert rendered.startswith("[Translate]")
