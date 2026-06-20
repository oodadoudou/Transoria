from __future__ import annotations

import pytest

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.runtime import Subtask, TaskRecord, TaskSnapshot


def _subtask(
    status: SubtaskStatus,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> Subtask:
    return Subtask(
        id=f"s-{status.value}-{input_tokens}",
        task_id="t",
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def test_task_record_round_trip() -> None:
    record = TaskRecord(
        id="t1",
        kind=TaskKind.TRANSLATION,
        status=TaskStatus.RUNNING,
        created_at="2026-04-27T00:00:00+00:00",
        updated_at="2026-04-27T00:05:00+00:00",
        metadata={"input_dir": "/tmp/in", "model_id": "m"},
    )

    assert TaskRecord.from_dict(record.to_dict()) == record
    assert TaskRecord.from_json(record.to_json()) == record


def test_task_record_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError, match="Invalid task kind"):
        TaskRecord.from_dict({"id": "t", "kind": "not-a-kind"})


def test_task_record_rejects_invalid_status() -> None:
    with pytest.raises(ValueError, match="Invalid task status"):
        TaskRecord.from_dict(
            {"id": "t", "kind": "translation", "status": "warp-speed"}
        )


def test_task_record_rejects_non_mapping_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        TaskRecord.from_dict(
            {"id": "t", "kind": "translation", "metadata": ["not", "mapping"]}
        )


def test_progress_counts_work_units_and_keeps_skipped_diagnostics() -> None:
    record = TaskRecord(id="t", kind=TaskKind.TRANSLATION)
    snapshot = TaskSnapshot(
        record=record,
        subtasks=(
            _subtask(SubtaskStatus.PENDING),
            _subtask(SubtaskStatus.PENDING, input_tokens=1),
            _subtask(SubtaskStatus.RUNNING),
            _subtask(SubtaskStatus.COMPLETED),
            _subtask(SubtaskStatus.COMPLETED, input_tokens=2),
            _subtask(SubtaskStatus.FAILED),
            _subtask(SubtaskStatus.SKIPPED),
        ),
    )

    progress = snapshot.progress()

    assert progress.total == 6
    assert progress.pending == 2
    assert progress.running == 1
    assert progress.completed == 2
    assert progress.failed == 1
    assert progress.skipped == 1
    assert progress.remaining == 3


def test_progress_rate_ignores_skipped_split_parents() -> None:
    record = TaskRecord(id="t", kind=TaskKind.TRANSLATION)
    snapshot = TaskSnapshot(
        record=record,
        subtasks=(
            _subtask(SubtaskStatus.COMPLETED),
            _subtask(SubtaskStatus.COMPLETED, input_tokens=1),
            _subtask(SubtaskStatus.SKIPPED),
        ),
    )

    progress = snapshot.progress(elapsed_seconds=10.0)

    assert progress.total == 2
    assert progress.rate_per_second == pytest.approx(0.2)


def test_usage_aggregates_token_counts() -> None:
    record = TaskRecord(id="t", kind=TaskKind.TRANSLATION)
    snapshot = TaskSnapshot(
        record=record,
        subtasks=(
            _subtask(
                SubtaskStatus.COMPLETED,
                input_tokens=10,
                output_tokens=20,
                cached_input_tokens=6,
            ),
            _subtask(
                SubtaskStatus.COMPLETED,
                input_tokens=3,
                output_tokens=4,
                cached_input_tokens=2,
            ),
        ),
    )

    usage = snapshot.usage()

    assert usage.input_tokens == 13
    assert usage.output_tokens == 24
    assert usage.cached_input_tokens == 8
    assert usage.total_tokens == 37


def test_is_finished_and_has_failures() -> None:
    record = TaskRecord(id="t", kind=TaskKind.TRANSLATION)
    finished_clean = TaskSnapshot(
        record=record,
        subtasks=(_subtask(SubtaskStatus.COMPLETED), _subtask(SubtaskStatus.SKIPPED)),
    )
    finished_with_failure = TaskSnapshot(
        record=record,
        subtasks=(_subtask(SubtaskStatus.COMPLETED), _subtask(SubtaskStatus.FAILED)),
    )

    assert finished_clean.is_finished() is True
    assert finished_clean.has_failures() is False
    assert finished_with_failure.is_finished() is False
    assert finished_with_failure.has_failures() is True
