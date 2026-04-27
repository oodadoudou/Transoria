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
