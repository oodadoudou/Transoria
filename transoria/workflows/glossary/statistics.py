"""Run statistics for the glossary extraction workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from transoria.llm.usage import TokenUsage


GLOSSARY_STATISTICS_FILENAME_JSON = "extraction-statistics.json"
GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS = "extraction-failed-subtasks.txt"


@dataclass(frozen=True)
class GlossaryFailedFile:
    path: str
    reason: str


@dataclass(frozen=True)
class GlossaryStatistics:
    started_at: str
    ended_at: str
    processed_files: tuple[str, ...] = ()
    glossary_outputs: tuple[str, ...] = ()
    candidate_count: int = 0
    final_entry_count: int = 0
    failed_subtasks: int = 0
    failed_files: tuple[GlossaryFailedFile, ...] = field(default_factory=tuple)
    decode_issue_count: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "processed_files": list(self.processed_files),
            "glossary_outputs": list(self.glossary_outputs),
            "candidate_count": self.candidate_count,
            "final_entry_count": self.final_entry_count,
            "failed_subtasks": self.failed_subtasks,
            "failed_files": [
                {"path": item.path, "reason": item.reason}
                for item in self.failed_files
            ],
            "decode_issue_count": self.decode_issue_count,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cached_input_tokens": self.usage.cached_input_tokens,
            "total_tokens": self.usage.total_tokens,
        }


def write_glossary_statistics(
    statistics: GlossaryStatistics,
    statistics_dir: Path,
    *,
    failed_subtask_details: tuple[tuple[str, str], ...] = (),
) -> Path:
    """Write the run statistics JSON and (when applicable) the failed
    subtask listing. The plain-text summary that previously sat next
    to the JSON has been removed — the JSON is the single source of
    truth and the text duplicate was noise users didn't read."""

    statistics_dir.mkdir(parents=True, exist_ok=True)
    json_path = statistics_dir / GLOSSARY_STATISTICS_FILENAME_JSON
    json_path.write_text(
        json.dumps(statistics.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failed_subtask_details:
        failed_path = statistics_dir / GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS
        blocks = [
            f"subtask: {subtask_id}\nerror: {error}"
            for subtask_id, error in failed_subtask_details
        ]
        failed_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return json_path


__all__ = [
    "GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS",
    "GLOSSARY_STATISTICS_FILENAME_JSON",
    "GlossaryFailedFile",
    "GlossaryStatistics",
    "write_glossary_statistics",
]
