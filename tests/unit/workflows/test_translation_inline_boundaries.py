import asyncio
from dataclasses import replace
import json

import pytest

from tests.unit.formats.test_formats_epub_parser import _write_minimal_epub
from tests.unit.workflows.test_workflows_translation_orchestrator import _build_config
from tests.unit.workflows.test_workflows_translation_runner import FakeTransport, _make_subtask, _model, _ok_body
from transoria.domain import Language
from transoria.llm import LlmClient
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.workflows.translation import ReplacementRule, TranslationSubtaskRunner
from transoria.workflows.translation.orchestrator import _prepare_segments, _scan_and_parse, _segment_metadata
from transoria.workflows.translation.runner import _decode_subtask_payload


def _inline_task(*, tracked=True):
    task = _make_subtask(sources=("First\nemphasis\nending.",))
    if tracked:
        task.request_payload["segments"][0]["inline_slot_count"] = 3
    else:
        task.request_payload["segments"][0].pop("inline_slot_count", None)
    return task


def _runner(responses, *, retries=0):
    transport = FakeTransport(responses=[
        TransportResult(200, _ok_body(json.dumps({"0": text}, ensure_ascii=False)))
        for text in responses
    ])
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport), model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH, target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=False, low_confidence_max_retries=retries,
    )
    return runner, transport


@pytest.mark.parametrize("candidate", ["前面强调词后面。", "前面\n强调词\n后面\n额外一行。"])
def test_inline_boundary_loss_is_flagged_even_without_optional_confidence_checks(candidate):
    runner, transport = _runner([candidate])
    result = asyncio.run(runner.run(_inline_task()))
    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == candidate
    assert "inline_format_boundary_mismatch" in payload["low_confidence"][0]["reasons"]
    assert len(transport.requests) == 1
    system = transport.requests[0]["payload"]["messages"][0]["content"]
    assert "Inline formatting boundaries" in system
    assert "Escaped \\n" in system


def test_inline_boundary_retry_recovers_format_without_replacing_other_segments():
    correct = "前面\n强调词\n后面。"
    runner, transport = _runner(["前面强调词后面。", correct], retries=1)
    result = asyncio.run(runner.run(_inline_task()))
    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": correct}
    assert not payload["low_confidence"]
    assert len(transport.requests) == 2


def test_persistent_missing_boundaries_retain_text_and_stop_at_existing_retry_budget():
    candidate = "前面强调词后面。"
    runner, transport = _runner([candidate, candidate], retries=1)
    result = asyncio.run(runner.run(_inline_task()))
    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == candidate
    assert "inline_format_boundary_mismatch" in payload["low_confidence"][0]["reasons"]
    assert len(transport.requests) == 2


def test_legacy_payload_and_txt_do_not_infer_inline_format_from_plain_newlines():
    task = _inline_task(tracked=False)
    _, metadata = _decode_subtask_payload(task.request_payload)
    assert metadata[0].inline_slot_count == 0
    runner, transport = _runner(["前面强调词后面。"])
    result = asyncio.run(runner.run(task))
    assert not json.loads(result.response_content)["low_confidence"]
    assert len(transport.requests) == 1


def test_epub_preparation_tracks_only_representable_inline_boundaries(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    _write_minimal_epub(
        input_dir / "book.epub",
        chapter_body=(
            '<p>Normal <em>emphasis</em> ending.</p>'
            '<p><span class="cap">A</span> story.</p>'
            '<p>Before<?dp n="1"?>after.</p>'
            '<p>Before <ruby>word<rt>reading</rt></ruby>after.</p>'
        ),
    )
    (input_dir / "plain.txt").write_text("ordinary text\nnext line", encoding="utf-8")
    config = replace(
        _build_config(input_dir=input_dir, output_dir=tmp_path / "out"),
        source_language=Language.ENGLISH,
    )
    parsed = _scan_and_parse(input_dir, buffer_epub_archives=False)
    prepared, _ = _prepare_segments(parsed, config)
    by_text = {p.original_text: p for p in prepared}
    assert by_text["Normal\nemphasis\nending."].inline_slot_count == 3
    assert by_text["A\nstory."].inline_slot_count == 2
    assert by_text["Beforeafter."].inline_slot_count == 0
    assert by_text["Beforewordafter."].inline_slot_count == 0
    assert by_text["ordinary text"].inline_slot_count == 0
    assert _segment_metadata(by_text["Normal\nemphasis\nending."])["inline_slot_count"] == 3

    rewritten, _ = _prepare_segments(
        parsed,
        replace(config, pre_replacements=(ReplacementRule(src="\n", dst=""),)),
    )
    assert [(p.segment_id, p.original_text) for p in rewritten] == [
        (p.segment_id, p.original_text) for p in prepared
    ]
    assert all(p.inline_slot_count == 0 for p in rewritten)
