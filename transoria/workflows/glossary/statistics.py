"""Run statistics for the glossary extraction workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from transoria.llm.usage import TokenUsage


GLOSSARY_STATISTICS_FILENAME_JSON = "extraction-statistics.json"
GLOSSARY_STATISTICS_FILENAME_TEXT = "extraction-statistics.txt"
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
            "total_tokens": self.usage.total_tokens,
        }


def write_glossary_statistics(
    statistics: GlossaryStatistics,
    output_dir: Path,
    *,
    write_text_summary: bool = True,
    failed_subtask_details: tuple[tuple[str, str], ...] = (),
) -> tuple[Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / GLOSSARY_STATISTICS_FILENAME_JSON
    json_path.write_text(
        json.dumps(statistics.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    text_path: Path | None = None
    if write_text_summary:
        text_path = output_dir / GLOSSARY_STATISTICS_FILENAME_TEXT
        text_path.write_text(_render_text_summary(statistics), encoding="utf-8")
    if failed_subtask_details:
        failed_path = output_dir / GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS
        blocks = [
            f"subtask: {subtask_id}\nerror: {error}"
            for subtask_id, error in failed_subtask_details
        ]
        failed_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return json_path, text_path


def _render_text_summary(stats: GlossaryStatistics) -> str:
    lines = [
        "Glossary Extraction Run Summary",
        "-------------------------------",
        f"Started: {stats.started_at}",
        f"Ended: {stats.ended_at}",
        "",
        f"Processed files: {len(stats.processed_files)}",
        f"Glossary outputs: {len(stats.glossary_outputs)}",
        "",
        f"Candidates discovered: {stats.candidate_count}",
        f"Final entries (after frequency filter): {stats.final_entry_count}",
        f"Decode issues skipped: {stats.decode_issue_count}",
        f"Failed subtasks: {stats.failed_subtasks}",
        "",
        f"Input tokens: {stats.usage.input_tokens}",
        f"Output tokens: {stats.usage.output_tokens}",
        f"Total tokens: {stats.usage.total_tokens}",
    ]
    if stats.failed_files:
        lines.append("")
        lines.append("Failed files:")
        lines.extend(f"- {item.path}: {item.reason}" for item in stats.failed_files)
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS",
    "GLOSSARY_STATISTICS_FILENAME_JSON",
    "GLOSSARY_STATISTICS_FILENAME_TEXT",
    "GlossaryFailedFile",
    "GlossaryStatistics",
    "write_glossary_statistics",
]
