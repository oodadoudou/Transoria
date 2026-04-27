from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.runtime import (
    ProgressEvent,
    Subtask,
    SubtaskResult,
    TaskCache,
    TaskExecutor,
    TaskRecord,
)


def _record(task_id: str = "t1") -> TaskRecord:
    return TaskRecord(
        id=task_id,
        kind=TaskKind.TRANSLATION,
        status=TaskStatus.PENDING,
        created_at="2026-04-27T00:00:00+00:00",
    )


def _seed(cache: TaskCache, task_id: str, count: int) -> None:
    cache.write_seed(
        _record(task_id),
        [Subtask(id=f"s{index}", task_id=task_id) for index in range(count)],
    )


@dataclass
class _FakeRunner:
    """Deterministic runner used by the executor tests."""

    delay_per_subtask: float = 0.0
    fail_ids: tuple[str, ...] = ()
    raise_exception: Callable[[Subtask], Exception] | None = None
    hang_event: asyncio.Event | None = None
    started: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    concurrent_high_water: int = field(default=0, init=False)
    _in_flight: int = field(default=0, init=False)

    async def run(self, subtask: Subtask) -> SubtaskResult:
        self.started.append(subtask.id)
        self._in_flight += 1
        self.concurrent_high_water = max(self.concurrent_high_water, self._in_flight)
        try:
            if self.hang_event is not None:
                await self.hang_event.wait()
            elif self.delay_per_subtask:
                await asyncio.sleep(self.delay_per_subtask)
            if self.raise_exception is not None:
                raise self.raise_exception(subtask)
            if subtask.id in self.fail_ids:
                raise RuntimeError(f"forced failure for {subtask.id}")
            self.completed.append(subtask.id)
            return SubtaskResult(
                response_content=f"out-{subtask.id}",
                input_tokens=10,
                output_tokens=20,
            )
        finally:
            self._in_flight -= 1


def test_run_completes_all_pending_and_persists_results(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=3)
    runner = _FakeRunner()
    executor = TaskExecutor(cache=cache, runner=runner, concurrency_limit=2, rpm_limit=0)

    snapshot = asyncio.run(executor.run("t1"))

    assert snapshot.record.status is TaskStatus.COMPLETED
    statuses = {s.status for s in snapshot.subtasks}
    assert statuses == {SubtaskStatus.COMPLETED}
    assert all(s.response_content == f"out-{s.id}" for s in snapshot.subtasks)
    assert snapshot.usage().total_tokens == 3 * 30
    # Cache state matches in-memory snapshot.
    assert cache.load("t1").record.status is TaskStatus.COMPLETED


def test_run_respects_concurrency_limit(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=6)
    runner = _FakeRunner(delay_per_subtask=0.05)
    executor = TaskExecutor(
        cache=cache, runner=runner, concurrency_limit=2, rpm_limit=0
    )

    asyncio.run(executor.run("t1"))

    assert runner.concurrent_high_water == 2
    assert len(runner.completed) == 6


def test_failed_subtasks_remain_failed_and_task_finalizes_failed(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=3)
    runner = _FakeRunner(fail_ids=("s1",))
    executor = TaskExecutor(cache=cache, runner=runner, concurrency_limit=2, rpm_limit=0)

    snapshot = asyncio.run(executor.run("t1"))

    assert snapshot.record.status is TaskStatus.FAILED
    by_id = {s.id: s for s in snapshot.subtasks}
    assert by_id["s0"].status is SubtaskStatus.COMPLETED
    assert by_id["s1"].status is SubtaskStatus.FAILED
    assert "RuntimeError" in by_id["s1"].last_error
    assert by_id["s2"].status is SubtaskStatus.COMPLETED


def test_rerun_failed_only_retouches_failed_subtasks(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=3)
    failing = _FakeRunner(fail_ids=("s1",))
    executor = TaskExecutor(cache=cache, runner=failing, concurrency_limit=2, rpm_limit=0)
    asyncio.run(executor.run("t1"))

    healing = _FakeRunner()
    executor = TaskExecutor(cache=cache, runner=healing, concurrency_limit=2, rpm_limit=0)
    snapshot = asyncio.run(executor.rerun_failed("t1"))

    assert snapshot.record.status is TaskStatus.COMPLETED
    assert healing.started == ["s1"]
    assert all(s.status is SubtaskStatus.COMPLETED for s in snapshot.subtasks)


def test_request_stop_transitions_through_stopping_to_stopped(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=4)
    hang_event = asyncio.Event()
    runner = _FakeRunner(hang_event=hang_event)
    executor = TaskExecutor(
        cache=cache, runner=runner, concurrency_limit=2, rpm_limit=0
    )

    observed_statuses: list[TaskStatus] = []

    async def scenario() -> None:
        async def watcher() -> None:
            # Wait until the runner has actually started workers, then stop.
            while len(runner.started) < 2:
                await asyncio.sleep(0)
            observed_statuses.append(cache.load_record("t1").status)
            executor.request_stop()

        watch_task = asyncio.create_task(watcher())
        snapshot = await executor.run("t1")
        await watch_task
        observed_statuses.append(snapshot.record.status)

    asyncio.run(scenario())

    assert observed_statuses[0] is TaskStatus.RUNNING
    assert observed_statuses[-1] is TaskStatus.STOPPED
    final = cache.load("t1")
    # In-flight subtasks must be rolled back to PENDING so resume is possible.
    by_id = {s.id: s for s in final.subtasks}
    assert by_id["s0"].status is SubtaskStatus.PENDING
    assert by_id["s1"].status is SubtaskStatus.PENDING
    # Subtasks that never started are still PENDING.
    assert by_id["s2"].status is SubtaskStatus.PENDING
    assert by_id["s3"].status is SubtaskStatus.PENDING


def test_stopped_task_resumes_from_cache_without_redoing_completed(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=3)
    # Pre-mark s0 as already completed (simulating a crash mid-run).
    cache.save_subtask(
        Subtask(
            id="s0",
            task_id="t1",
            status=SubtaskStatus.COMPLETED,
            response_content="prior",
            input_tokens=5,
            output_tokens=7,
        )
    )

    runner = _FakeRunner()
    executor = TaskExecutor(cache=cache, runner=runner, concurrency_limit=2, rpm_limit=0)
    snapshot = asyncio.run(executor.run("t1"))

    assert sorted(runner.started) == ["s1", "s2"]
    assert snapshot.record.status is TaskStatus.COMPLETED
    s0 = next(s for s in snapshot.subtasks if s.id == "s0")
    assert s0.response_content == "prior"  # untouched


def test_progress_listener_fires_per_status_change(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=2)
    events: list[ProgressEvent] = []
    runner = _FakeRunner()
    executor = TaskExecutor(
        cache=cache,
        runner=runner,
        concurrency_limit=1,
        rpm_limit=0,
        progress=events.append,
    )

    asyncio.run(executor.run("t1"))

    # Each subtask fires twice: once on RUNNING, once on COMPLETED.
    assert len(events) == 4
    assert {event.changed_subtask_id for event in events} == {"s0", "s1"}


def test_run_with_no_pending_subtasks_finalizes_status(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.write_seed(
        _record("t1"),
        [Subtask(id="s0", task_id="t1", status=SubtaskStatus.COMPLETED)],
    )
    runner = _FakeRunner()
    executor = TaskExecutor(cache=cache, runner=runner, concurrency_limit=1, rpm_limit=0)

    snapshot = asyncio.run(executor.run("t1"))

    assert runner.started == []
    assert snapshot.record.status is TaskStatus.COMPLETED


def test_attempt_count_increments_per_run(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=1)
    failing_runner = _FakeRunner(fail_ids=("s0",))
    executor = TaskExecutor(cache=cache, runner=failing_runner, concurrency_limit=1, rpm_limit=0)
    asyncio.run(executor.run("t1"))

    after_first = cache.load_subtasks("t1")[0]
    assert after_first.attempt_count == 1

    healing_runner = _FakeRunner()
    executor = TaskExecutor(cache=cache, runner=healing_runner, concurrency_limit=1, rpm_limit=0)
    asyncio.run(executor.rerun_failed("t1"))

    after_rerun = cache.load_subtasks("t1")[0]
    assert after_rerun.attempt_count == 2
