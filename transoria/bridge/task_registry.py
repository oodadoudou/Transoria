"""Thread-safe registry of in-flight tasks for the bridge layer.

Each ``RunningTask`` wraps the lifecycle of one task thread:

- Stores references to the ``TaskCache`` and the background ``Thread``.
- Receives the ``TaskExecutor`` via ``set_executor`` (called from inside the
  thread once the orchestrator creates it).
- Exposes ``request_stop()`` so bridge handlers can signal cooperative
  cancellation without caring whether the executor exists yet.
- Tracks done/error state for the polling endpoint.

``TaskRegistry`` is the single global store; ``build_default_router`` creates
one instance and threads it through all handlers that need it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transoria.runtime.cache import TaskCache
    from transoria.runtime.executor import TaskExecutor


@dataclass
class RunningTask:
    task_id: str
    kind: str
    cache: "TaskCache"
    created_at: str
    thread: "threading.Thread | None" = None

    _executor: "TaskExecutor | None" = field(default=None, init=False, repr=False)
    _done: bool = field(default=False, init=False, repr=False)
    _error: "BaseException | None" = field(default=None, init=False, repr=False)
    _stop_flag: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def set_executor(self, executor: "TaskExecutor") -> None:
        """Store a reference to the executor once the orchestrator creates it.

        Called from the background thread before ``executor.run()`` is awaited.
        """
        with self._lock:
            self._executor = executor

    def request_stop(self) -> None:
        """Signal cooperative stop. No-op if the executor does not exist yet.

        Sets ``_stop_flag`` for synchronous (non-LLM) workers like the
        replacement task; LLM-backed tasks rely on the executor's
        :meth:`request_stop` to cancel in-flight subtasks.
        """
        with self._lock:
            executor = self._executor
            self._stop_flag = True
        if executor is not None:
            executor.request_stop()

    def request_pause(self) -> None:
        """Signal cooperative pause. Requires an executor; replacement
        tasks (sync) should never call this — TaskService rejects
        pause for replacement before reaching here."""
        with self._lock:
            executor = self._executor
        if executor is not None:
            executor.request_pause()

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_flag

    def mark_done(self, error: "BaseException | None" = None) -> None:
        """Record completion (success or failure) from the background thread."""
        with self._lock:
            self._done = True
            self._error = error

    @property
    def is_done(self) -> bool:
        with self._lock:
            return self._done

    @property
    def last_error(self) -> "BaseException | None":
        with self._lock:
            return self._error


class TaskRegistry:
    """Thread-safe map of task_id → RunningTask."""

    def __init__(self) -> None:
        self._tasks: dict[str, RunningTask] = {}
        self._lock = threading.Lock()

    def add(self, task: RunningTask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task

    def get(self, task_id: str) -> "RunningTask | None":
        with self._lock:
            return self._tasks.get(task_id)

    def list_by_kind(self, kind: str) -> list[RunningTask]:
        """Return tasks of ``kind`` sorted by ``created_at`` descending."""
        with self._lock:
            tasks = [t for t in self._tasks.values() if t.kind == kind]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks


__all__ = ["RunningTask", "TaskRegistry"]
