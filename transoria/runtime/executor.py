"""TaskExecutor: walks a task's pending subtasks with concurrency, RPM
throttling, persistence, and cooperative cancellation.

The executor is provider-agnostic. Workflow layers (Translation, Glossary)
implement :class:`SubtaskRunner` to bridge subtask payloads to LLM calls.
The executor only knows: take pending subtasks → run them under limits →
persist progress → respond to stop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

from transoria.domain import SubtaskStatus, TaskStatus
from transoria.runtime.cache import TaskCache
from transoria.runtime.rate_limit import RpmLimiter
from transoria.runtime.subtask import Subtask
from transoria.runtime.task_record import TaskSnapshot


@dataclass(frozen=True)
class SubtaskResult:
    """What a runner returns when a subtask succeeds."""

    response_content: str
    input_tokens: int = 0
    output_tokens: int = 0


class SubtaskRunner(Protocol):
    """Workflow-level adapter that knows how to execute one subtask payload."""

    async def run(self, subtask: Subtask) -> SubtaskResult: ...


@dataclass(frozen=True)
class ProgressEvent:
    snapshot: TaskSnapshot
    changed_subtask_id: str
    timestamp: str


ProgressListener = Callable[[ProgressEvent], None]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TaskExecutor:
    cache: TaskCache
    runner: SubtaskRunner
    concurrency_limit: int = 2
    rpm_limit: int = 60
    progress: ProgressListener | None = None
    clock: Callable[[], str] = _utc_now_iso

    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        """Signal cooperative shutdown. Safe to call from any thread/coroutine."""

        self._stop_event.set()

    @property
    def is_stopping(self) -> bool:
        return self._stop_event.is_set()

    async def run(self, task_id: str) -> TaskSnapshot:
        """Execute all PENDING subtasks of ``task_id`` and return the final snapshot.

        Idempotent: a snapshot with no pending subtasks is returned unchanged
        and the task status is set to ``COMPLETED`` if every subtask is in a
        terminal state, ``FAILED`` if any subtask is in ``FAILED``, otherwise
        left as-is.
        """

        snapshot = self.cache.load(task_id)
        pending = [s for s in snapshot.subtasks if s.status is SubtaskStatus.PENDING]
        if not pending:
            return self._finalize(task_id, stopped=False)

        self._stop_event = asyncio.Event()
        self._update_record_status(task_id, TaskStatus.RUNNING)

        await self._drive(task_id, pending)

        return self._finalize(task_id, stopped=self._stop_event.is_set())

    async def rerun_failed(self, task_id: str) -> TaskSnapshot:
        """Reset every FAILED subtask to PENDING, then :meth:`run`."""

        for subtask in self.cache.load_subtasks(task_id):
            if subtask.status is SubtaskStatus.FAILED:
                self.cache.save_subtask(
                    replace(
                        subtask,
                        status=SubtaskStatus.PENDING,
                        last_error="",
                    )
                )
        return await self.run(task_id)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    async def _drive(self, task_id: str, pending: list[Subtask]) -> None:
        semaphore = asyncio.Semaphore(self.concurrency_limit)
        limiter = RpmLimiter(limit=self.rpm_limit)

        workers = [
            asyncio.create_task(self._execute_one(task_id, subtask, semaphore, limiter))
            for subtask in pending
        ]
        stop_handler = asyncio.create_task(self._handle_stop(task_id, workers))

        try:
            await asyncio.gather(*workers, return_exceptions=True)
        finally:
            stop_handler.cancel()
            try:
                await stop_handler
            except asyncio.CancelledError:
                pass

    async def _handle_stop(
        self, task_id: str, workers: list[asyncio.Task[None]]
    ) -> None:
        await self._stop_event.wait()
        self._update_record_status(task_id, TaskStatus.STOPPING)
        for worker in workers:
            if not worker.done():
                worker.cancel()

    async def _execute_one(
        self,
        task_id: str,
        subtask: Subtask,
        semaphore: asyncio.Semaphore,
        limiter: RpmLimiter,
    ) -> None:
        async with semaphore:
            if self._stop_event.is_set():
                return
            try:
                await limiter.acquire()
            except asyncio.CancelledError:
                return
            if self._stop_event.is_set():
                return

            running = replace(
                subtask,
                status=SubtaskStatus.RUNNING,
                attempt_count=subtask.attempt_count + 1,
                last_error="",
            )
            self.cache.save_subtask(running)
            self._fire_progress(task_id, running.id)

            try:
                result = await self.runner.run(running)
            except asyncio.CancelledError:
                # Stop requested while in-flight: leave the subtask in PENDING
                # so the next run picks it up. Re-raise so the executor knows
                # this worker did not complete naturally.
                rolled_back = replace(
                    running,
                    status=SubtaskStatus.PENDING,
                )
                self.cache.save_subtask(rolled_back)
                self._fire_progress(task_id, rolled_back.id)
                raise
            except Exception as exc:
                # Prefix the structured ``code`` (set by LlmRequestError and
                # subclasses) when present so the frontend can localise
                # without parsing the human-readable message.
                code_prefix = ""
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code:
                    code_prefix = f"[{code}] "
                failed = replace(
                    running,
                    status=SubtaskStatus.FAILED,
                    last_error=f"{code_prefix}{type(exc).__name__}: {exc}",
                )
                self.cache.save_subtask(failed)
                self._fire_progress(task_id, failed.id)
                return

            completed = replace(
                running,
                status=SubtaskStatus.COMPLETED,
                response_content=result.response_content,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                last_error="",
            )
            self.cache.save_subtask(completed)
            self._fire_progress(task_id, completed.id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_record_status(self, task_id: str, status: TaskStatus) -> None:
        record = self.cache.load_record(task_id)
        if record.status is status:
            return
        self.cache.save_task(record.with_status(status).with_updated_at(self.clock()))

    def _finalize(self, task_id: str, *, stopped: bool) -> TaskSnapshot:
        snapshot = self.cache.load(task_id)
        progress = snapshot.progress()
        if stopped:
            final = TaskStatus.STOPPED
        elif progress.failed > 0 and progress.pending == 0 and progress.running == 0:
            final = TaskStatus.FAILED
        elif progress.pending == 0 and progress.running == 0 and progress.failed == 0:
            final = TaskStatus.COMPLETED
        else:
            # Mixed terminal/non-terminal — keep current status.
            return snapshot
        self.cache.save_task(
            snapshot.record.with_status(final).with_updated_at(self.clock())
        )
        return self.cache.load(task_id)

    def _fire_progress(self, task_id: str, subtask_id: str) -> None:
        if self.progress is None:
            return
        snapshot = self.cache.load(task_id)
        try:
            self.progress(
                ProgressEvent(
                    snapshot=snapshot,
                    changed_subtask_id=subtask_id,
                    timestamp=self.clock(),
                )
            )
        except Exception:  # pragma: no cover — listener errors must not break runtime
            pass


__all__ = [
    "ProgressEvent",
    "ProgressListener",
    "SubtaskResult",
    "SubtaskRunner",
    "TaskExecutor",
]
