"""Subtask: a single LLM request unit within a task."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Mapping

from transoria.domain import SubtaskStatus


@dataclass(frozen=True)
class Subtask:
    """One persistable LLM request inside a task.

    ``request_payload`` is opaque JSON-serializable input that the registered
    :class:`SubtaskRunner` knows how to interpret. The runtime does not look
    inside it. ``response_content`` stores the raw model output once the
    subtask completes; decoding into typed records happens at the workflow
    layer (Translation/Glossary).
    """

    id: str
    task_id: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    request_payload: Mapping[str, object] = field(default_factory=dict)
    response_content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    attempt_count: int = 0
    last_error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status.value,
            "request_payload": dict(self.request_payload),
            "response_content": self.response_content,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Subtask:
        try:
            status = SubtaskStatus(str(data.get("status", SubtaskStatus.PENDING.value)))
        except ValueError as exc:
            raise ValueError(f"Invalid subtask status: {data!r}") from exc
        payload = data.get("request_payload", {})
        if not isinstance(payload, Mapping):
            raise ValueError(f"request_payload must be a mapping: {payload!r}")
        return cls(
            id=str(data["id"]),
            task_id=str(data["task_id"]),
            status=status,
            request_payload=dict(payload),
            response_content=str(data.get("response_content", "")),
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            attempt_count=int(data.get("attempt_count", 0)),
            last_error=str(data.get("last_error", "")),
        )

    @classmethod
    def from_json(cls, raw: str) -> Subtask:
        return cls.from_dict(json.loads(raw))

    def with_status(self, status: SubtaskStatus) -> Subtask:
        return replace(self, status=status)


__all__ = ["Subtask"]
