"""Run statistics file for the translation workflow.

Per ``docs/translation-module-design.md``, every run writes a JSON statistics
file (and an optional human-readable text summary) to the output directory so
the UI can surface failed files, token totals, and output paths after the
task settles.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from transoria.llm.usage import TokenUsage


STATISTICS_FILENAME_JSON = "translation-statistics.json"
STATISTICS_FILENAME_FAILED_SUBTASKS = "translation-failed-subtasks.txt"


@dataclass(frozen=True)
class FailedFile:
    path: str
    reason: str
    code: str = "unknown"
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LowConfidenceSegment:
    segment_id: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class TranslationStatistics:
    started_at: str
    ended_at: str
    processed_files: tuple[str, ...] = ()
    translated_outputs: tuple[str, ...] = ()
    bilingual_outputs: tuple[str, ...] = ()
    total_segments: int = 0
    completed_segments: int = 0
    failed_subtasks: int = 0
    failed_files: tuple[FailedFile, ...] = field(default_factory=tuple)
    low_confidence_segments: tuple[LowConfidenceSegment, ...] = field(
        default_factory=tuple
    )
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "processed_files": list(self.processed_files),
            "translated_outputs": list(self.translated_outputs),
            "bilingual_outputs": list(self.bilingual_outputs),
            "total_segments": self.total_segments,
            "completed_segments": self.completed_segments,
            "failed_subtasks": self.failed_subtasks,
            "failed_files": [
                {
                    "path": item.path,
                    "reason": item.reason,
                    "code": item.code,
                    "details": item.details,
                }
                for item in self.failed_files
            ],
            "low_confidence_segments": [
                {"segment_id": item.segment_id, "reasons": list(item.reasons)}
                for item in self.low_confidence_segments
            ],
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cached_input_tokens": self.usage.cached_input_tokens,
            "total_tokens": self.usage.total_tokens,
        }


def write_translation_statistics(
    statistics: TranslationStatistics,
    output_dir: Path,
    *,
    failed_subtask_details: tuple[tuple[str, str], ...] = (),
) -> Path:
    """Persist the statistics JSON to ``output_dir`` and (when applicable)
    a text artifact listing failed subtask errors. Returns the JSON path."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / STATISTICS_FILENAME_JSON
    json_path.write_text(
        json.dumps(statistics.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failed_subtask_details:
        failed_path = output_dir / STATISTICS_FILENAME_FAILED_SUBTASKS
        blocks = [
            f"subtask: {subtask_id}\nerror: {error}"
            for subtask_id, error in failed_subtask_details
        ]
        failed_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    else:
        (output_dir / STATISTICS_FILENAME_FAILED_SUBTASKS).unlink(missing_ok=True)
    return json_path


__all__ = [
    "FailedFile",
    "LowConfidenceSegment",
    "STATISTICS_FILENAME_FAILED_SUBTASKS",
    "STATISTICS_FILENAME_JSON",
    "TranslationStatistics",
    "write_translation_statistics",
]
