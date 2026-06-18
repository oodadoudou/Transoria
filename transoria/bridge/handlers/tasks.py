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

_LLM_DOMAINS: tuple[str, ...] = ("translation", "glossary", "glossary_review")


def _expect_task_id(payload: Mapping[str, object]) -> str:
    return expect_string(payload, "task_id")


def _expect_request_id(payload: Mapping[str, object]) -> str:
    return expect_string(payload, "request_id")


def _optional_string(payload: Mapping[str, object], key: str) -> str:
    raw = payload.get(key, "")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise BridgeError.invalid_argument(
            f"{key} must be a string.",
            field=key,
        )
    return raw


def _expect_int_list(payload: Mapping[str, object], key: str) -> list[int]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise BridgeError.invalid_argument(
            f"{key} must be a list of integers.",
            field=key,
        )
    out: list[int] = []
    for index, value in enumerate(raw):
        if isinstance(value, bool):
            raise BridgeError.invalid_argument(
                f"{key}[{index}] must be an integer.",
                field=key,
            )
        try:
            out.append(int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise BridgeError.invalid_argument(
                f"{key}[{index}] must be an integer.",
                field=key,
            ) from exc
    return out


def _optional_limit(payload: Mapping[str, object]) -> int | None:
    return _optional_non_negative_int(payload, "limit")


def _optional_offset(payload: Mapping[str, object]) -> int:
    return _optional_non_negative_int(payload, "offset") or 0


def _optional_non_negative_int(
    payload: Mapping[str, object], key: str
) -> int | None:
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BridgeError.invalid_argument(
            f"{key} must be an integer.",
            field=key,
        ) from exc
    if value < 0:
        raise BridgeError.invalid_argument(
            f"{key} must be >= 0.",
            field=key,
        )
    return value


def _optional_request_status(payload: Mapping[str, object]) -> str:
    raw = payload.get("status", "")
    if raw is None or raw == "all":
        return ""
    if not isinstance(raw, str):
        raise BridgeError.invalid_argument(
            "status must be a string.",
            field="status",
        )
    if raw not in {"", "running", "completed", "failed", "cancelled"}:
        raise BridgeError.invalid_argument(
            "status must be one of running, completed, failed, cancelled, or all.",
            field="status",
        )
    return raw


def _build_handlers(
    service: TaskService, *, kind: str
) -> dict[str, object]:
    starters = {
        "translation": service.start_translation,
        "glossary": service.start_glossary,
        "glossary_review": service.start_glossary_review,
    }
    starter = starters[kind]

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

    def read_request_events(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_request_events(
            kind=kind,
            task_id=_expect_task_id(payload),
            limit=_optional_limit(payload),
            offset=_optional_offset(payload),
            status=_optional_request_status(payload),
        )

    def read_artifacts(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_artifacts(
            kind=kind, task_id=_expect_task_id(payload)
        )

    handlers = {
        f"{kind}.start_task": start_task,
        f"{kind}.stop_task": stop_task,
        f"{kind}.pause_task": pause_task,
        f"{kind}.continue_task": continue_task,
        f"{kind}.probe_continuable": probe_continuable,
        f"{kind}.read_snapshot": read_snapshot,
        f"{kind}.list_recent_tasks": list_recent_tasks,
        f"{kind}.list_failed_subtasks": list_failed_subtasks,
        f"{kind}.read_request_events": read_request_events,
        f"{kind}.read_artifacts": read_artifacts,
    }
    if kind == "glossary_review":
        handlers[f"{kind}.discover_inputs"] = (
            lambda payload: service.discover_glossary_review_inputs(
                input_folder=_optional_string(payload, "input_folder"),
                output_filename=_optional_string(payload, "output_filename"),
            )
        )
        handlers[f"{kind}.read_report"] = lambda payload: service.read_glossary_review_report(
            task_id=_expect_task_id(payload)
        )
        handlers[f"{kind}.read_final"] = lambda payload: service.read_glossary_review_final(
            task_id=_expect_task_id(payload)
        )
        handlers[f"{kind}.update_final_row"] = (
            lambda payload: service.update_glossary_review_final_row(
                task_id=_expect_task_id(payload),
                row_index=int(payload.get("row_index", 0)),
                src=str(payload.get("src", "")),
                dst=str(payload.get("dst", "")),
                info=str(payload.get("info", "")),
                delete=bool(payload.get("delete", False)),
            )
        )
        handlers[f"{kind}.delete_final_rows"] = (
            lambda payload: service.delete_glossary_review_final_rows(
                task_id=_expect_task_id(payload),
                row_indices=_expect_int_list(payload, "row_indices"),
            )
        )
        handlers[f"{kind}.restore_deleted_report_row"] = (
            lambda payload: service.restore_glossary_review_deleted_report_row(
                task_id=_expect_task_id(payload),
                src=str(payload.get("src", "")),
                dst=str(payload.get("dst", "")),
                info=str(payload.get("info", "")),
                frequency=int(payload.get("frequency", 0)),
            )
        )
    return handlers


def _build_cache_management_handlers(
    service: TaskService,
) -> dict[str, object]:
    """Kind-agnostic handlers exposed under the ``tasks.*`` namespace."""

    def summarize_caches(_payload: Mapping[str, object]) -> dict[str, object]:
        return service.summarize_caches()

    def purge_caches(payload: Mapping[str, object]) -> dict[str, object]:
        scope = expect_string(payload, "scope")
        raw_days = payload.get("days")
        days: int | None
        if raw_days is None:
            days = None
        else:
            try:
                days = int(raw_days)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise BridgeError.invalid_argument(
                    "days must be an integer.",
                    field="days",
                ) from exc
        return service.purge_caches(scope=scope, days=days)

    return {
        "tasks.summarize_caches": summarize_caches,
        "tasks.purge_caches": purge_caches,
    }


def register(router: BridgeRouter, *, service: TaskService) -> None:
    """Register translation + glossary task handlers."""

    for kind in _LLM_DOMAINS:
        for method, handler in _build_handlers(service, kind=kind).items():
            router.register(method, handler)  # type: ignore[arg-type]
    for method, handler in _build_cache_management_handlers(service).items():
        router.register(method, handler)  # type: ignore[arg-type]


__all__ = ["register"]
