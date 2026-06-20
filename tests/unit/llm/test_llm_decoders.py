from __future__ import annotations

from transoria.llm.decoders import (
    decode_glossary_jsonl,
    decode_translation_jsonl,
)


def test_translation_decoder_parses_plain_jsonl() -> None:
    raw = '{"0":"hello"}\n{"1":"world"}\n'

    result = decode_translation_jsonl(raw)

    assert [(line.index, line.text) for line in result.lines] == [
        (0, "hello"),
        (1, "world"),
    ]
    assert result.issues == ()


def test_translation_decoder_strips_jsonline_fence() -> None:
    raw = "Sure, here you go:\n```jsonline\n" '{"0":"hi"}\n{"1":"there"}\n' "```\n"

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["hi", "there"]


def test_translation_decoder_strips_thinking_block() -> None:
    raw = (
        "<why>\n[Global Context]: ...\n[Edge Cases]: ...\n</why>\n"
        '{"0":"hello"}\n{"1":"world"}\n'
    )

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["hello", "world"]


def test_translation_decoder_strips_think_block() -> None:
    raw = (
        "<think>\nreason first\n</think>\n"
        '{"0":"hello"}\n{"1":"world"}\n'
    )

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["hello", "world"]


def test_translation_decoder_repairs_broken_json() -> None:
    raw = '{"0": "hello",}\n{"1": "world"}\n'  # trailing comma is malformed standard JSON

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["hello", "world"]


def test_translation_decoder_reports_invalid_lines_without_raising() -> None:
    raw = '{"0":"hello"}\nnot json at all\n{"oops":[]}\n{"abc":"x"}\n{"2":"end"}\n'

    result = decode_translation_jsonl(raw)

    assert [line.index for line in result.lines] == [0, 2]
    reasons = " ".join(issue.reason for issue in result.issues)
    assert "non-numeric" in reasons or "not a single-key" in reasons


def test_translation_decoder_dedupes_repeated_indices() -> None:
    raw = '{"0":"first"}\n{"0":"dup"}\n{"1":"ok"}\n'

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["first", "ok"]
    assert any("duplicate" in issue.reason for issue in result.issues)


def test_glossary_decoder_normalizes_type_to_info() -> None:
    raw = (
        '{"src":"신해범","dst":"申海范","type":"Male Name"}\n'
        '{"src":"하늘","dst":"天空","type":"Other"}\n'
    )

    result = decode_glossary_jsonl(raw)

    assert [(e.src, e.dst, e.info) for e in result.entries] == [
        ("신해범", "申海范", "Male Name"),
        ("하늘", "天空", "Other"),
    ]


def test_glossary_decoder_accepts_info_field_directly() -> None:
    raw = '{"src":"a","dst":"b","info":"Location"}\n'

    result = decode_glossary_jsonl(raw)

    assert result.entries[0].info == "Location"


def test_glossary_decoder_skips_missing_src_or_dst() -> None:
    raw = (
        '{"src":"","dst":"x","type":"t"}\n'
        '{"src":"y","dst":"","type":"t"}\n'
        '{"src":"ok","dst":"good","type":"t"}\n'
    )

    result = decode_glossary_jsonl(raw)

    assert [e.src for e in result.entries] == ["ok"]
    assert len(result.issues) == 2


def test_glossary_decoder_strips_thinking_and_fence() -> None:
    raw = (
        "<why>\nreasoning\n</why>\n"
        "```jsonline\n"
        '{"src":"a","dst":"b","type":"t"}\n'
        "```"
    )

    result = decode_glossary_jsonl(raw)

    assert [(e.src, e.dst, e.info) for e in result.entries] == [("a", "b", "t")]


def test_decode_glossary_jsonl_salvages_canonical_table_layout() -> None:
    """Standard layout: ``| src | dst | type | note |``. Entries map
    cleanly via the header, separator and prose are skipped, AND when
    salvage succeeds the table-shaped lines are removed from the
    ``issues`` list so the report reflects only true format failures
    (genuine prose, not extracted table rows)."""

    raw = (
        "好的，将进行实体提取。\n"
        "\n"
        "| 韩文原文 | 中文译名 | 分类 | 备注 |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| **미아** | 米亚 | 男性角色 | 主角 |\n"
        "| 로건 | 罗根 | 男性角色 | 国王 |\n"
        "| 벨로리아 왕국 | 贝洛里亚王国 | 命名地理 | 王国 |\n"
    )

    result = decode_glossary_jsonl(raw)

    extracted = [(e.src, e.dst, e.info) for e in result.entries]
    assert ("미아", "米亚", "男性角色") in extracted
    assert ("로건", "罗根", "男性角色") in extracted
    assert ("벨로리아 왕국", "贝洛里亚王国", "命名地理") in extracted
    # Header / separator rows must NOT bleed in as entries.
    assert not any(src in {"韩文原文", ":---"} for src, *_ in extracted)
    # Issues list, post-salvage, must contain only the prose preamble —
    # not the header row, not the separator, not the data rows that
    # contributed to ``entries``.
    issue_lines = [issue.line for issue in result.issues]
    assert all("|" not in line for line in issue_lines), (
        f"table-shaped lines leaked into issues: {issue_lines}"
    )


def test_decode_glossary_jsonl_salvage_handles_shuffled_columns() -> None:
    """Column order ``| 分类 | 原文 | 译名 | 备注 |`` must still produce
    correct ``(src, dst, info)`` tuples. Earlier salvage took cells by
    position and produced ``(src='男性角色', dst='로건')`` corruption."""

    raw = (
        "| 分类 | 韩文原文 | 中文译名 | 备注 |\n"
        "|------|----------|----------|------|\n"
        "| 男性角色 | 로건 | 罗根 | 父亲 |\n"
        "| 命名地理 | 벨로리아 왕국 | 贝洛利亚王国 | 故事舞台 |\n"
    )

    result = decode_glossary_jsonl(raw)

    extracted = [(e.src, e.dst, e.info) for e in result.entries]
    assert ("로건", "罗根", "男性角色") in extracted
    assert ("벨로리아 왕국", "贝洛利亚王国", "命名地理") in extracted


def test_decode_glossary_jsonl_salvage_drops_rows_without_header() -> None:
    """A bare table (no header row anywhere) is unsafe to map; salvage
    refuses guesses and the decoder yields zero entries."""

    raw = (
        "| 男性角色 | 로건 | 罗根 | 父亲 |\n"
        "| 男性角色 | 미아 | 米亚 | 儿子 |\n"
    )

    result = decode_glossary_jsonl(raw)

    assert result.entries == ()


def test_decode_glossary_jsonl_yields_only_jsonline_rows_when_mixed() -> None:
    """Mixed JSONL + markdown: only the JSONL row counts. The trailing
    table is reported as issues, never converted into a fake entry."""

    raw = (
        '{"src":"미아","dst":"米亚","type":"男性角色"}\n'
        "| 로건 | 罗根 | 男性角色 |\n"
    )

    result = decode_glossary_jsonl(raw)

    assert [(e.src, e.dst, e.info) for e in result.entries] == [
        ("미아", "米亚", "男性角色")
    ]


def test_decode_glossary_jsonl_salvage_resets_on_blank_line() -> None:
    """Two tables with different header layouts in one response — the
    second header must replace the first mapping so rows under it use
    the right column order."""

    raw = (
        "| 韩文原文 | 中文译名 | 分类 |\n"
        "| :--- | :--- | :--- |\n"
        "| 미아 | 米亚 | 男性角色 |\n"
        "\n"
        "| 分类 | 韩文原文 | 中文译名 |\n"
        "| :--- | :--- | :--- |\n"
        "| 命名地理 | 벨로리아 | 贝洛利亚 |\n"
    )

    result = decode_glossary_jsonl(raw)

    extracted = [(e.src, e.dst, e.info) for e in result.entries]
    assert ("미아", "米亚", "男性角色") in extracted
    assert ("벨로리아", "贝洛利亚", "命名地理") in extracted
# Whole-response fallback (decoder Layer 1)
#
# These cover the drift modes where per-line JSONL parsing yields zero
# rows — typically because the model emitted one big multi-line JSON
# value instead of one independent object per line. Each shape was seen
# in real failure logs.


def test_translation_decoder_recovers_pretty_printed_object() -> None:
    raw = '{\n  "0": "hello",\n  "1": "world"\n}\n'

    result = decode_translation_jsonl(raw)

    assert [(line.index, line.text) for line in result.lines] == [
        (0, "hello"),
        (1, "world"),
    ]
    # Per-line "not a single-key JSON object" issues are suppressed
    # because the whole-response fallback succeeded.
    assert all(
        i.reason != "not a single-key JSON object" for i in result.issues
    )


def test_translation_decoder_recovers_compact_one_line_object() -> None:
    raw = '{"0":"hello","1":"world","2":"!"}'

    result = decode_translation_jsonl(raw)

    assert [(line.index, line.text) for line in result.lines] == [
        (0, "hello"),
        (1, "world"),
        (2, "!"),
    ]


def test_translation_decoder_recovers_pretty_object_inside_fence() -> None:
    raw = (
        "Here is the translation:\n"
        "```jsonline\n"
        "{\n"
        '  "0": "hello",\n'
        '  "1": "world"\n'
        "}\n"
        "```\n"
    )

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["hello", "world"]


def test_translation_decoder_recovers_json_array_positionally() -> None:
    raw = '["alpha", "beta", "gamma"]\n'

    result = decode_translation_jsonl(raw)

    assert [(line.index, line.text) for line in result.lines] == [
        (0, "alpha"),
        (1, "beta"),
        (2, "gamma"),
    ]


def test_translation_decoder_unwraps_single_key_wrapper() -> None:
    raw = '{"translations": {"0": "hello", "1": "world"}}\n'

    result = decode_translation_jsonl(raw)

    assert [(line.index, line.text) for line in result.lines] == [
        (0, "hello"),
        (1, "world"),
    ]


def test_translation_decoder_repair_loads_trailing_comma_object() -> None:
    raw = '{\n  "0": "hello",\n  "1": "world",\n}\n'

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["hello", "world"]


def test_translation_decoder_recovers_object_with_thinking_prefix() -> None:
    raw = (
        "<think>\nplanning translation\n</think>\n"
        '{\n  "0": "hi",\n  "1": "there"\n}\n'
    )

    result = decode_translation_jsonl(raw)

    assert [line.text for line in result.lines] == ["hi", "there"]


def test_translation_decoder_returns_empty_for_pure_prose() -> None:
    raw = (
        "Hello, world!\n"
        "I am a literary translation.\n"
        "Each line is prose, no JSON.\n"
    )

    result = decode_translation_jsonl(raw)

    assert result.lines == ()
    # Decoder records issues so the runner can decide retry / rescue.
    assert len(result.issues) >= 3


def test_translation_decoder_skips_non_string_values_in_object() -> None:
    """A wrapper object whose values are integers/lists is not a
    translation payload — the harvester must not coerce them into
    bogus strings."""

    raw = '{"meta": {"version": 1}, "count": 7}\n'

    result = decode_translation_jsonl(raw)

    assert result.lines == ()
