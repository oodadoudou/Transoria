"""Tests for post-translation glossary reference reports."""

from __future__ import annotations

import json

import pytest

from transoria.domain import SubtaskStatus
from transoria.runtime.subtask import Subtask
from transoria.workflows.translation.glossary_report import (
    build_glossary_application_report,
    term_variant,
    write_glossary_application_report,
)
from transoria.workflows.translation.rules import GlossaryEntry


def _completed_subtask() -> Subtask:
    return Subtask(
        id="chunk-00000",
        task_id="translation-test",
        status=SubtaskStatus.COMPLETED,
        request_payload={
            "glossary_entries": [
                {
                    "src": "마수",
                    "dst": "魔兽",
                    "info": "monster-like creature",
                    "enabled": True,
                },
                {
                    "src": "왕자",
                    "dst": "王太子",
                    "enabled": True,
                },
                {
                    "src": "disabled",
                    "dst": "禁用",
                    "enabled": False,
                },
            ],
            "segments": [
                {
                    "segment_id": "0:1",
                    "prompt_text": "마수는 왕자를 보았다.",
                    "original_text": "마수는 왕자를 보았다.",
                },
                {
                    "segment_id": "0:2",
                    "prompt_text": "아무 용어도 없다.",
                    "original_text": "아무 용어도 없다.",
                },
            ],
        },
    )


def test_glossary_report_is_local_reference_not_translation_gate(tmp_path):
    report = build_glossary_application_report(
        [_completed_subtask()],
        {
            "0:1": "魔兽看见了王世子。",
            "0:2": "没有任何术语。",
        },
    )

    assert report.total_matches == 2
    assert report.target_term_present_matches == 1
    assert report.review_suggested_matches == 1
    assert report.segments_with_matches == 1
    assert report.segments_with_review_suggestions == 1

    paths = write_glossary_application_report(report, tmp_path)
    markdown = paths.markdown_path.read_text(encoding="utf-8")
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

    assert "不会额外调用模型，也不会修改译文" in markdown
    assert "疑似另一种写法（参考）" in markdown
    assert "译文写作 `王世子`" in markdown
    assert "建议复核" not in markdown
    assert "不代表翻译错误" in markdown
    assert payload["total_matches"] == 2
    assert payload["review_suggested_matches"] == 1


def test_glossary_report_ignores_unfinished_subtasks():
    failed = Subtask(
        id="chunk-failed",
        task_id="translation-test",
        status=SubtaskStatus.FAILED,
        request_payload={
            "glossary_entries": [{"src": "마수", "dst": "魔兽"}],
            "segments": [{"segment_id": "0:1", "prompt_text": "마수"}],
        },
    )

    report = build_glossary_application_report(
        [_completed_subtask(), failed],
        {"0:1": "魔物看见了王子。"},
    )

    assert report.total_matches == 2


@pytest.mark.parametrize(
    ("dst", "translation", "expected"),
    [
        ("申海范", "申海帆回来了。", "申海帆"),
        ("申海范", "海范回来了。", ""),
        ("申海范", "对海范说。", ""),
        ("申海范", "他回来了。", ""),
        ("申海范", "신해범回来了。", "신해범"),
        ("魔兽", "魔物来了。", ""),
    ],
)
def test_term_variant_only_reports_another_rendering(dst, translation, expected):
    assert term_variant(GlossaryEntry(src="신해범", dst=dst), translation) == expected
