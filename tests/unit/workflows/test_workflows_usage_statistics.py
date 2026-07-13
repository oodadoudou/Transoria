from __future__ import annotations

from pathlib import Path

from transoria.llm import TokenUsage
from transoria.workflows.glossary.statistics import GlossaryStatistics
from transoria.workflows.translation.statistics import (
    STATISTICS_FILENAME_FAILED_SUBTASKS,
    TranslationStatistics,
    write_translation_statistics,
)


def test_translation_statistics_exports_cached_input_tokens() -> None:
    stats = TranslationStatistics(
        started_at="2026-06-15T00:00:00+00:00",
        ended_at="2026-06-15T00:01:00+00:00",
        usage=TokenUsage(
            input_tokens=100,
            output_tokens=40,
            cached_input_tokens=80,
        ),
    )

    assert stats.to_dict()["cached_input_tokens"] == 80


def test_translation_statistics_removes_stale_failed_subtask_report(
    tmp_path: Path,
) -> None:
    stats = TranslationStatistics(
        started_at="2026-07-13T19:13:41+00:00",
        ended_at="2026-07-13T19:18:08+00:00",
    )

    write_translation_statistics(
        stats,
        tmp_path,
        failed_subtask_details=(("chunk-00265", "HTTP 400"),),
    )
    failed_path = tmp_path / STATISTICS_FILENAME_FAILED_SUBTASKS
    assert failed_path.exists()

    write_translation_statistics(stats, tmp_path)

    assert not failed_path.exists()


def test_glossary_statistics_exports_cached_input_tokens() -> None:
    stats = GlossaryStatistics(
        started_at="2026-06-15T00:00:00+00:00",
        ended_at="2026-06-15T00:01:00+00:00",
        usage=TokenUsage(
            input_tokens=70,
            output_tokens=30,
            cached_input_tokens=55,
        ),
    )

    assert stats.to_dict()["cached_input_tokens"] == 55
