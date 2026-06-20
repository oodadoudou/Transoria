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
from transoria.runtime.task_timing import (
    RUNTIME_ELAPSED_SECONDS_KEY,
    RUNTIME_STARTED_AT_KEY,
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
                cached_input_tokens=4,
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
    assert snapshot.usage().cached_input_tokens == 3 * 4
    # Cache state matches in-memory snapshot.
    assert cache.load("t1").record.status is TaskStatus.COMPLETED


def test_executor_records_accumulated_runtime_seconds(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=1)
    runner = _FakeRunner()
    ticks = iter(
        [
            "2026-04-27T00:01:00+00:00",
            "2026-04-27T00:01:10+00:00",
            "2026-04-27T00:01:20+00:00",
        ]
    )
    executor = TaskExecutor(
        cache=cache,
        runner=runner,
        concurrency_limit=1,
        rpm_limit=0,
        clock=lambda: next(ticks),
    )

    snapshot = asyncio.run(executor.run("t1"))

    assert snapshot.record.status is TaskStatus.COMPLETED
    assert snapshot.record.metadata[RUNTIME_ELAPSED_SECONDS_KEY] == 20.0
    assert RUNTIME_STARTED_AT_KEY not in snapshot.record.metadata


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


def test_run_paces_launches_when_spacing_is_configured(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=3)
    now = 0.0
    sleeps: list[float] = []
    starts: list[float] = []

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay
        await asyncio.sleep(0)

    class RecordingRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            starts.append(now)
            return SubtaskResult(response_content=f"out-{subtask.id}")

    executor = TaskExecutor(
        cache=cache,
        runner=RecordingRunner(),
        concurrency_limit=3,
        rpm_limit=0,
        launch_spacing_seconds=0.5,
        launch_clock=lambda: now,
        launch_sleep=fake_sleep,
    )

    asyncio.run(executor.run("t1"))

    assert starts == [0.0, 0.5, 1.0]
    assert sleeps == [0.5, 0.5]


def test_run_uses_bounded_worker_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=50)
    runner = _FakeRunner()
    executor = TaskExecutor(
        cache=cache, runner=runner, concurrency_limit=3, rpm_limit=0
    )
    real_create_task = asyncio.create_task
    created = 0

    def track_create_task(coro: object) -> asyncio.Task[object]:
        nonlocal created
        created += 1
        return real_create_task(coro)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_task", track_create_task)

    snapshot = asyncio.run(executor.run("t1"))

    assert snapshot.record.status is TaskStatus.COMPLETED
    assert created == 5  # 3 workers + stop handler + pause handler
    assert len(runner.completed) == 50


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


def test_running_subtask_timeout_fails_instead_of_hanging(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=1)
    runner = _FakeRunner(hang_event=asyncio.Event())
    executor = TaskExecutor(
        cache=cache,
        runner=runner,
        concurrency_limit=1,
        rpm_limit=0,
        subtask_timeout_seconds=0.01,
    )

    snapshot = asyncio.run(executor.run("t1"))

    assert snapshot.record.status is TaskStatus.FAILED
    subtask = snapshot.subtasks[0]
    assert subtask.status is SubtaskStatus.FAILED
    assert "Subtask exceeded 0s timeout" in subtask.last_error


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


def test_stop_with_drain_lets_in_flight_finish_naturally(tmp_path: Path) -> None:
    """Production stop semantics: when ``stop_drain_seconds > 0``,
    in-flight LLM calls are allowed to complete naturally instead of
    being cancelled mid-call. Subtasks that complete during the drain
    window get persisted as COMPLETED (no wasted tokens), not rolled
    back to PENDING."""

    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=4)
    hang_event = asyncio.Event()
    runner = _FakeRunner(hang_event=hang_event)
    executor = TaskExecutor(
        cache=cache,
        runner=runner,
        concurrency_limit=2,
        rpm_limit=0,
        stop_drain_seconds=5.0,
    )

    async def scenario() -> None:
        async def watcher() -> None:
            while len(runner.started) < 2:
                await asyncio.sleep(0)
            executor.request_stop()
            # Give _handle_stop a beat to flip status to STOPPING and
            # enter its drain wait. Then release the in-flight runners
            # so they complete naturally inside the drain window.
            await asyncio.sleep(0.1)
            hang_event.set()

        watch_task = asyncio.create_task(watcher())
        await executor.run("t1")
        await watch_task

    asyncio.run(scenario())

    final = cache.load("t1")
    by_id = {s.id: s for s in final.subtasks}
    # In-flight subtasks completed naturally during the drain window.
    assert by_id["s0"].status is SubtaskStatus.COMPLETED
    assert by_id["s1"].status is SubtaskStatus.COMPLETED
    # Queued subtasks (never started) stay PENDING because the stop
    # gate stopped them before the LLM call.
    assert by_id["s2"].status is SubtaskStatus.PENDING
    assert by_id["s3"].status is SubtaskStatus.PENDING
    assert final.record.status is TaskStatus.STOPPED


def test_stop_drain_falls_back_to_cancel_after_deadline(tmp_path: Path) -> None:
    """If in-flight runners do not finish within ``stop_drain_seconds``
    we hard-cancel them so a wedged HTTP call cannot block Stop
    forever. Cancelled in-flight subtasks roll back to PENDING."""

    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=2)
    hang_event = asyncio.Event()  # never fired
    runner = _FakeRunner(hang_event=hang_event)
    executor = TaskExecutor(
        cache=cache,
        runner=runner,
        concurrency_limit=2,
        rpm_limit=0,
        stop_drain_seconds=0.3,  # short so the test stays fast
    )

    async def scenario() -> None:
        async def watcher() -> None:
            while len(runner.started) < 2:
                await asyncio.sleep(0)
            executor.request_stop()

        watch_task = asyncio.create_task(watcher())
        await executor.run("t1")
        await watch_task

    asyncio.run(scenario())

    final = cache.load("t1")
    by_id = {s.id: s for s in final.subtasks}
    # In-flight subtasks were force-cancelled past the drain deadline.
    assert by_id["s0"].status is SubtaskStatus.PENDING
    assert by_id["s1"].status is SubtaskStatus.PENDING
    assert final.record.status is TaskStatus.STOPPED


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


def test_request_pause_transitions_through_pausing_to_paused(
    tmp_path: Path,
) -> None:
    """Pause lets in-flight finish, blocks new dispatches, and ends in
    ``PAUSED`` (not ``STOPPED``). Subtasks that hadn't started yet stay
    PENDING; in-flight ones complete normally."""

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
            # Wait for two workers to be in-flight, then pause.
            while len(runner.started) < 2:
                await asyncio.sleep(0)
            observed_statuses.append(cache.load_record("t1").status)
            executor.request_pause()
            # Let the in-flight runners complete.
            await asyncio.sleep(0)
            hang_event.set()

        watch_task = asyncio.create_task(watcher())
        snapshot = await executor.run("t1")
        await watch_task
        observed_statuses.append(snapshot.record.status)

    asyncio.run(scenario())

    assert observed_statuses[0] is TaskStatus.RUNNING
    assert observed_statuses[-1] is TaskStatus.PAUSED
    final = cache.load("t1")
    by_id = {s.id: s for s in final.subtasks}
    # The two in-flight runners completed before pause took effect.
    assert by_id["s0"].status is SubtaskStatus.COMPLETED
    assert by_id["s1"].status is SubtaskStatus.COMPLETED
    # The two queued workers were cancelled at the gate; PENDING for continue.
    assert by_id["s2"].status is SubtaskStatus.PENDING
    assert by_id["s3"].status is SubtaskStatus.PENDING


def test_paused_task_resumes_from_cache_on_second_run(tmp_path: Path) -> None:
    """A second ``run()`` with the same task_id picks up the pending
    subtasks left by an earlier pause and runs them to completion."""

    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=3)
    # Pre-mark s0 as completed (simulates the in-flight worker that
    # finished before pause settled).
    cache.save_subtask(
        Subtask(
            id="s0",
            task_id="t1",
            status=SubtaskStatus.COMPLETED,
            response_content="prior",
        )
    )
    # And mark the record as PAUSED to mirror the real flow.
    record = cache.load_record("t1")
    cache.save_task(record.with_status(TaskStatus.PAUSED))

    runner = _FakeRunner()
    executor = TaskExecutor(
        cache=cache, runner=runner, concurrency_limit=2, rpm_limit=0
    )
    snapshot = asyncio.run(executor.run("t1"))

    assert snapshot.record.status is TaskStatus.COMPLETED
    # s0 was already done; the executor must not re-run it.
    assert "s0" not in runner.started
    assert set(runner.started) == {"s1", "s2"}


def test_executor_fails_dangling_pending_when_worker_exits_unexpectedly(
    tmp_path: Path,
) -> None:
    cache = TaskCache(root=tmp_path)
    _seed(cache, "t1", count=1)
    runner = _FakeRunner()
    executor = TaskExecutor(cache=cache, runner=runner, concurrency_limit=1, rpm_limit=0)

    async def crash_before_runner(*_args: object) -> None:
        raise RuntimeError("worker crashed before runner")

    executor._execute_one = crash_before_runner  # type: ignore[method-assign]

    snapshot = asyncio.run(executor.run("t1"))

    assert snapshot.record.status is TaskStatus.FAILED
    subtask = snapshot.subtasks[0]
    assert subtask.status is SubtaskStatus.FAILED
    assert "executor.dangling_pending" in subtask.last_error


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
