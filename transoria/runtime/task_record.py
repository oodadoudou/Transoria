"""TaskRecord and TaskSnapshot: the persistable shape of a runtime task."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Mapping

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.llm.usage import TokenUsage
from transoria.runtime.subtask import Subtask


@dataclass(frozen=True)
class TaskRecord:
    """Header information for one runtime task.

    The record is metadata only; subtasks are stored separately by the cache so
    a single subtask write does not rewrite the whole task. ``metadata`` is an
    opaque dict that workflow layers (Translation, Glossary) use to remember
    the configuration snapshot needed to resume a stopped run.
    """

    id: str
    kind: TaskKind
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TaskRecord:
        try:
            kind = TaskKind(str(data["kind"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid task kind: {data!r}") from exc
        try:
            status = TaskStatus(str(data.get("status", TaskStatus.PENDING.value)))
        except ValueError as exc:
            raise ValueError(f"Invalid task status: {data!r}") from exc
        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError(f"metadata must be a mapping: {metadata!r}")
        return cls(
            id=str(data["id"]),
            kind=kind,
            status=status,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            metadata=dict(metadata),
        )

    @classmethod
    def from_json(cls, raw: str) -> TaskRecord:
        return cls.from_dict(json.loads(raw))

    def with_status(self, status: TaskStatus) -> TaskRecord:
        return replace(self, status=status)

    def with_updated_at(self, timestamp: str) -> TaskRecord:
        return replace(self, updated_at=timestamp)


@dataclass(frozen=True)
class ProgressStats:
    total: int
    pending: int
    running: int
    completed: int
    failed: int
    skipped: int
    rate_per_second: float = 0.0

    @property
    def remaining(self) -> int:
        return self.pending + self.running


@dataclass(frozen=True)
class TaskSnapshot:
    """Immutable read of a task's persisted state.

    Aggregations live here, not on the executor, so callers (UI, tests, the
    statistics file writer) all read the same numbers.
    """

    record: TaskRecord
    subtasks: tuple[Subtask, ...] = ()

    def progress(self, *, elapsed_seconds: float | None = None) -> ProgressStats:
        counts: dict[SubtaskStatus, int] = {status: 0 for status in SubtaskStatus}
        for subtask in self.subtasks:
            counts[subtask.status] += 1
        rate = 0.0
        if elapsed_seconds is not None and elapsed_seconds > 0:
            settled = counts[SubtaskStatus.COMPLETED] + counts[SubtaskStatus.SKIPPED]
            if settled > 0:
                rate = settled / elapsed_seconds
        return ProgressStats(
            total=len(self.subtasks),
            pending=counts[SubtaskStatus.PENDING],
            running=counts[SubtaskStatus.RUNNING],
            completed=counts[SubtaskStatus.COMPLETED],
            failed=counts[SubtaskStatus.FAILED],
            skipped=counts[SubtaskStatus.SKIPPED],
            rate_per_second=rate,
        )

    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for subtask in self.subtasks:
            total = total + TokenUsage(
                input_tokens=subtask.input_tokens,
                output_tokens=subtask.output_tokens,
            )
        return total

    def is_finished(self) -> bool:
        return all(
            subtask.status
            in (SubtaskStatus.COMPLETED, SubtaskStatus.SKIPPED)
            for subtask in self.subtasks
        )

    def has_failures(self) -> bool:
        return any(
            subtask.status is SubtaskStatus.FAILED for subtask in self.subtasks
        )


__all__ = ["ProgressStats", "TaskRecord", "TaskSnapshot"]
