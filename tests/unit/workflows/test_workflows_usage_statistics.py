from __future__ import annotations

from transoria.llm import TokenUsage
from transoria.workflows.glossary.statistics import GlossaryStatistics
from transoria.workflows.translation.statistics import TranslationStatistics


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
