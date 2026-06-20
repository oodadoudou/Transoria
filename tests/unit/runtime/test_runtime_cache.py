from __future__ import annotations

from pathlib import Path

import pytest

from transoria.runtime import cache as cache_module
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


def test_load_recent_request_events_reads_tail_only(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(_record())
    for index in range(10):
        cache.append_request_event(
            "task-1",
            {
                "request_id": f"req-{index}",
                "status": "completed",
                "created_at": f"2026-04-27T00:00:{index:02d}+00:00",
            },
        )

    events, truncated = cache.load_recent_request_events(
        "task-1",
        max_lines=3,
        max_bytes=10_000,
    )

    assert truncated is True
    assert tuple(event["request_id"] for event in events) == (
        "req-7",
        "req-8",
        "req-9",
    )


def test_append_request_event_trims_oversized_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, "_REQUEST_EVENTS_MAX_BYTES", 700)
    monkeypatch.setattr(cache_module, "_REQUEST_EVENTS_TRIM_BYTES", 350)
    cache = TaskCache(root=tmp_path)
    cache.save_task(_record())

    for index in range(30):
        cache.append_request_event(
            "task-1",
            {
                "request_id": f"req-{index}",
                "status": "completed",
                "response_text": "x" * 40,
            },
        )

    path = cache.task_dir("task-1") / "request-events.jsonl"
    events = cache.load_request_events("task-1")

    assert path.stat().st_size <= 700
    assert events[-1]["request_id"] == "req-29"
    assert "req-0" not in {str(event["request_id"]) for event in events}


def test_atomic_write_retries_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = TaskCache(root=tmp_path)
    original_replace = cache_module.os.replace
    calls = 0

    def flaky_replace(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("locked")
        original_replace(src, dst)

    monkeypatch.setattr(cache_module.os, "replace", flaky_replace)
    monkeypatch.setattr(cache_module.time, "sleep", lambda _delay: None)

    cache.write_seed(_record(), [_subtask("task-1", "a")])

    assert calls >= 2
    assert cache.load("task-1").record.id == "task-1"


def test_load_tolerates_legacy_json_with_trailing_duplicate_object(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.write_seed(_record(), [_subtask("task-1", "a")])
    task_path = cache.task_dir("task-1") / "task.json"
    task_path.write_text(
        task_path.read_text(encoding="utf-8") + "\n{}",
        encoding="utf-8",
    )

    assert cache.load_record("task-1").id == "task-1"


def test_load_subtasks_tolerates_legacy_json_with_trailing_duplicate_object(
    tmp_path: Path,
) -> None:
    cache = TaskCache(root=tmp_path)
    cache.write_seed(_record(), [_subtask("task-1", "a")])
    subtask_path = cache.subtask_path("task-1", "a")
    subtask_path.write_text(
        subtask_path.read_text(encoding="utf-8") + "\n{}",
        encoding="utf-8",
    )

    assert cache.load("task-1").subtasks[0].id == "a"


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


def test_list_tasks_skips_unknown_legacy_task_kind(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(_record("current"))
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "task.json").write_text(
        '{"id":"legacy","kind":"removed_kind","status":"completed"}',
        encoding="utf-8",
    )

    records = cache.list_tasks()

    assert tuple(r.id for r in records) == ("current",)


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
