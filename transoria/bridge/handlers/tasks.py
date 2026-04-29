"""Bridge handlers for the translation/glossary task domains.

The handlers are thin shells that forward to :class:`TaskService`. The service
owns task lifecycle, cache, and registry; the handlers only translate the
JSON wire shape into method calls and back.

Replacement is wired separately under
:mod:`transoria.bridge.handlers.replacement` because that domain also exposes
the rule-import/validate methods that share no infrastructure with the task
runtime.
"""

from __future__ import annotations

from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.bridge.task_service import TaskService

_LLM_DOMAINS: tuple[str, ...] = ("translation", "glossary")


def _expect_task_id(payload: Mapping[str, object]) -> str:
    return expect_string(payload, "task_id")


def _expect_request_id(payload: Mapping[str, object]) -> str:
    return expect_string(payload, "request_id")


def _optional_limit(payload: Mapping[str, object]) -> int | None:
    raw = payload.get("limit")
    if raw is None:
        return None
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BridgeError.invalid_argument(
            "limit must be an integer.",
            field="limit",
        ) from exc
    if value < 0:
        raise BridgeError.invalid_argument(
            "limit must be >= 0.",
            field="limit",
        )
    return value


def _build_handlers(
    service: TaskService, *, kind: str
) -> dict[str, object]:
    starter = service.start_translation if kind == "translation" else service.start_glossary

    def start_task(payload: Mapping[str, object]) -> dict[str, object]:
        request_id = _expect_request_id(payload)
        return starter(request_id)

    def stop_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.stop_task(kind=kind, task_id=_expect_task_id(payload))

    def pause_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.pause_task(kind=kind, task_id=_expect_task_id(payload))

    def continue_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.continue_task(kind=kind, task_id=_expect_task_id(payload))

    def probe_continuable(_payload: Mapping[str, object]) -> dict[str, object]:
        return service.probe_continuable(kind=kind)

    def read_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_snapshot(kind=kind, task_id=_expect_task_id(payload))

    def list_recent_tasks(payload: Mapping[str, object]) -> dict[str, object]:
        return service.list_recent_tasks(kind=kind, limit=_optional_limit(payload))

    def list_failed_subtasks(payload: Mapping[str, object]) -> dict[str, object]:
        return service.list_failed_subtasks(
            kind=kind, task_id=_expect_task_id(payload)
        )

    def read_artifacts(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_artifacts(
            kind=kind, task_id=_expect_task_id(payload)
        )

    return {
        f"{kind}.start_task": start_task,
        f"{kind}.stop_task": stop_task,
        f"{kind}.pause_task": pause_task,
        f"{kind}.continue_task": continue_task,
        f"{kind}.probe_continuable": probe_continuable,
        f"{kind}.read_snapshot": read_snapshot,
        f"{kind}.list_recent_tasks": list_recent_tasks,
        f"{kind}.list_failed_subtasks": list_failed_subtasks,
        f"{kind}.read_artifacts": read_artifacts,
    }


def register(router: BridgeRouter, *, service: TaskService) -> None:
    """Register translation + glossary task handlers."""

    for kind in _LLM_DOMAINS:
        for method, handler in _build_handlers(service, kind=kind).items():
            router.register(method, handler)  # type: ignore[arg-type]


__all__ = ["register"]
