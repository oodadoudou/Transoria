"""Task snapshot handlers for the runtime domains.

Step 4 wires the bridge surface for task discovery (``list_recent_tasks``)
and read-only inspection (``read_snapshot``, ``list_failed_subtasks``).
The actual workflow runtime lands in Step 7; until then there is no
running task to inspect, so:

- ``*.list_recent_tasks`` returns ``{"tasks": []}``.
- ``*.read_snapshot`` and ``*.list_failed_subtasks`` raise
  ``bridge.not_found`` for any ``task_id`` because none have been started.

This shape lets the frontend exercise the bridge end-to-end (idle render,
empty/idle defaults preserved, no fake data) before the workflow
implementations are wired up.
"""

from __future__ import annotations

from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter

_DOMAINS: tuple[str, ...] = ("translation", "glossary", "replacement")


def _expect_task_id(payload: Mapping[str, object]) -> str:
    return expect_string(payload, "task_id")


def _list_recent_tasks(_payload: Mapping[str, object]) -> dict[str, object]:
    return {"tasks": []}


def _read_snapshot_not_found(payload: Mapping[str, object]) -> dict[str, object]:
    task_id = _expect_task_id(payload)
    raise BridgeError.not_found(
        f"No active task with id {task_id!r}.",
        details={"task_id": task_id},
    )


def _list_failures_not_found(payload: Mapping[str, object]) -> dict[str, object]:
    task_id = _expect_task_id(payload)
    raise BridgeError.not_found(
        f"No active task with id {task_id!r}.",
        details={"task_id": task_id},
    )


def register(router: BridgeRouter) -> None:
    for domain in _DOMAINS:
        router.register(f"{domain}.list_recent_tasks", _list_recent_tasks)
        router.register(f"{domain}.read_snapshot", _read_snapshot_not_found)
        router.register(f"{domain}.list_failed_subtasks", _list_failures_not_found)


__all__ = ["register"]
