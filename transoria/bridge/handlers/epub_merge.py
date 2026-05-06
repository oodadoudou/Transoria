from __future__ import annotations

from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.bridge.task_service import TaskService


def register(router: BridgeRouter, *, service: TaskService) -> None:
    def preview(payload: Mapping[str, object]) -> dict[str, object]:
        options = payload.get("options", {})
        if not isinstance(options, Mapping):
            raise BridgeError.invalid_argument(
                "options must be an object.",
                field="options",
            )
        return service.preview_epub_merge(
            input_dir=expect_string(payload, "input_dir"),
            options=options,
        )

    def start_task(payload: Mapping[str, object]) -> dict[str, object]:
        request_id = expect_string(payload, "request_id")
        input_dir = expect_string(payload, "input_dir")
        output_path = expect_string(payload, "output_path")
        options = payload.get("options", {})
        raw_actions = payload.get("actions")
        if not isinstance(options, Mapping):
            raise BridgeError.invalid_argument(
                "options must be an object.",
                field="options",
            )
        if not isinstance(raw_actions, list):
            raise BridgeError.invalid_argument(
                "actions must be a list.",
                field="actions",
            )
        actions: list[Mapping[str, object]] = []
        for index, raw in enumerate(raw_actions):
            if not isinstance(raw, Mapping):
                raise BridgeError.invalid_argument(
                    f"actions[{index}] must be an object.",
                    field="actions",
                )
            actions.append(raw)
        return service.start_epub_merge(
            request_id=request_id,
            input_dir=input_dir,
            output_path=output_path,
            options=options,
            actions=actions,
        )

    def stop_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.stop_task(
            kind="epub_merge", task_id=expect_string(payload, "task_id")
        )

    def pause_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.pause_task(
            kind="epub_merge", task_id=expect_string(payload, "task_id")
        )

    def continue_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.continue_task(
            kind="epub_merge", task_id=expect_string(payload, "task_id")
        )

    def probe_continuable(_payload: Mapping[str, object]) -> dict[str, object]:
        return service.probe_continuable(kind="epub_merge")

    def read_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_snapshot(
            kind="epub_merge", task_id=expect_string(payload, "task_id")
        )

    def list_recent_tasks(payload: Mapping[str, object]) -> dict[str, object]:
        raw_limit = payload.get("limit")
        if raw_limit is None:
            limit = None
        else:
            try:
                limit = int(raw_limit)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise BridgeError.invalid_argument(
                    "limit must be an integer.",
                    field="limit",
                ) from exc
            if limit < 0:
                raise BridgeError.invalid_argument(
                    "limit must be >= 0.",
                    field="limit",
                )
        return service.list_recent_tasks(kind="epub_merge", limit=limit)

    def read_artifacts(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_artifacts(
            kind="epub_merge", task_id=expect_string(payload, "task_id")
        )

    def read_report(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_epub_merge_report(
            task_id=expect_string(payload, "task_id")
        )

    def list_failed_subtasks(payload: Mapping[str, object]) -> dict[str, object]:
        return service.list_failed_subtasks(
            kind="epub_merge", task_id=expect_string(payload, "task_id")
        )

    router.register("epub_merge.preview", preview)
    router.register("epub_merge.start_task", start_task)
    router.register("epub_merge.stop_task", stop_task)
    router.register("epub_merge.pause_task", pause_task)
    router.register("epub_merge.continue_task", continue_task)
    router.register("epub_merge.probe_continuable", probe_continuable)
    router.register("epub_merge.read_snapshot", read_snapshot)
    router.register("epub_merge.list_recent_tasks", list_recent_tasks)
    router.register("epub_merge.read_artifacts", read_artifacts)
    router.register("epub_merge.read_report", read_report)
    router.register("epub_merge.list_failed_subtasks", list_failed_subtasks)


__all__ = ["register"]
