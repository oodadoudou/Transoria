from __future__ import annotations

from pathlib import Path

import pytest

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.runtime import Subtask, TaskCache, TaskNotFoundError, TaskRecord


def _record(task_id: str = "task-1") -> TaskRecord:
    return TaskRecord(
        id=task_id,
        kind=TaskKind.TRANSLATION,
        status=TaskStatus.PENDING,
        created_at="2026-04-27T00:00:00+00:00",
        metadata={"input_dir": "/tmp/in"},
    )


def _subtask(task_id: str, sid: str, status: SubtaskStatus = SubtaskStatus.PENDING) -> Subtask:
    return Subtask(id=sid, task_id=task_id, status=status)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    record = _record()
    subtasks = [_subtask("task-1", "a"), _subtask("task-1", "b")]

    cache.write_seed(record, subtasks)
    snapshot = cache.load("task-1")

    assert snapshot.record == record
    assert tuple(s.id for s in snapshot.subtasks) == ("a", "b")


def test_load_raises_for_unknown_task(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)

    with pytest.raises(TaskNotFoundError):
        cache.load("missing")


def test_save_subtask_does_not_rewrite_other_subtasks(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.write_seed(_record(), [_subtask("task-1", "a"), _subtask("task-1", "b")])

    sibling_path = cache.subtask_path("task-1", "b")
    sibling_mtime_before = sibling_path.stat().st_mtime_ns

    cache.save_subtask(
        _subtask("task-1", "a", status=SubtaskStatus.COMPLETED)
    )

    assert sibling_path.stat().st_mtime_ns == sibling_mtime_before


def test_atomic_write_does_not_leave_partial_file(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.write_seed(_record(), [_subtask("task-1", "a")])

    task_dir = cache.task_dir("task-1")
    leftover = list(task_dir.rglob("*.tmp"))

    assert leftover == []


def test_list_tasks_returns_records_sorted(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(_record("alpha"))
    cache.save_task(_record("bravo"))
    cache.save_task(_record("charlie"))

    records = cache.list_tasks()

    assert tuple(r.id for r in records) == ("alpha", "bravo", "charlie")


def test_list_tasks_empty_root_returns_empty_tuple(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path / "does-not-exist-yet")

    assert cache.list_tasks() == ()


def test_delete_removes_task_tree(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.write_seed(_record(), [_subtask("task-1", "a")])

    cache.delete("task-1")

    with pytest.raises(TaskNotFoundError):
        cache.load("task-1")


def test_delete_unknown_raises(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)

    with pytest.raises(TaskNotFoundError):
        cache.delete("missing")


def test_invalid_task_id_rejected(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)

    with pytest.raises(ValueError, match="Invalid task id"):
        cache.task_dir("../escape")
    with pytest.raises(ValueError, match="Invalid task id"):
        cache.task_dir("")


def test_invalid_subtask_id_rejected(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)

    with pytest.raises(ValueError, match="Invalid subtask id"):
        cache.subtask_path("task-1", "with/slash")
