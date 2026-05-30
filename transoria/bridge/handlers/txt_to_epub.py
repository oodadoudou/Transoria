from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.bridge.task_service import TaskService
from transoria.tools.txt_to_epub import (
    TxtToEpubOptions,
    list_epub_styles,
    list_toc_presets,
    scan_txt_toc,
)


def register(router: BridgeRouter, *, service: TaskService) -> None:
    def list_styles(_payload: Mapping[str, object]) -> dict[str, object]:
        return list_epub_styles()

    def list_presets(_payload: Mapping[str, object]) -> dict[str, object]:
        return list_toc_presets()

    def scan_toc(payload: Mapping[str, object]) -> dict[str, object]:
        raw_custom = payload.get("custom_rules", [])
        custom_rules: list[Mapping[str, object]] = []
        if isinstance(raw_custom, list):
            for index, raw in enumerate(raw_custom):
                if not isinstance(raw, Mapping):
                    raise BridgeError.invalid_argument(
                        f"custom_rules[{index}] must be an object.",
                        field="custom_rules",
                    )
                custom_rules.append(raw)
        try:
            return scan_txt_toc(
                Path(expect_string(payload, "source_path")),
                preset_id=str(payload.get("preset_id", "markdown")),
                custom_rules=custom_rules,
                advanced_pattern=str(payload.get("advanced_pattern", "")),
            )
        except (OSError, ValueError, re.error) as exc:
            raise BridgeError.invalid_argument(
                str(exc),
                field="source_path",
            ) from exc

    def preview(payload: Mapping[str, object]) -> dict[str, object]:
        options = _options(payload)
        return service.preview_txt_to_epub(options=options.to_dict())

    def start_task(payload: Mapping[str, object]) -> dict[str, object]:
        request_id = expect_string(payload, "request_id")
        options = _options(payload)
        return service.start_txt_to_epub(
            request_id=request_id,
            options=options.to_dict(),
        )

    def stop_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.stop_task(
            kind="txt_to_epub", task_id=expect_string(payload, "task_id")
        )

    def read_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_snapshot(
            kind="txt_to_epub", task_id=expect_string(payload, "task_id")
        )

    def list_recent_tasks(payload: Mapping[str, object]) -> dict[str, object]:
        raw_limit = payload.get("limit")
        limit: int | None
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
        return service.list_recent_tasks(kind="txt_to_epub", limit=limit)

    def probe_continuable(_payload: Mapping[str, object]) -> dict[str, object]:
        return service.probe_continuable(kind="txt_to_epub")

    def read_artifacts(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_artifacts(
            kind="txt_to_epub", task_id=expect_string(payload, "task_id")
        )

    def read_report(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_txt_to_epub_report(
            task_id=expect_string(payload, "task_id")
        )

    def list_failed_subtasks(payload: Mapping[str, object]) -> dict[str, object]:
        return service.list_failed_subtasks(
            kind="txt_to_epub", task_id=expect_string(payload, "task_id")
        )

    router.register("txt_to_epub.list_styles", list_styles)
    router.register("txt_to_epub.list_presets", list_presets)
    router.register("txt_to_epub.scan_toc", scan_toc)
    router.register("txt_to_epub.preview", preview)
    router.register("txt_to_epub.start_task", start_task)
    router.register("txt_to_epub.stop_task", stop_task)
    router.register("txt_to_epub.read_snapshot", read_snapshot)
    router.register("txt_to_epub.list_recent_tasks", list_recent_tasks)
    router.register("txt_to_epub.probe_continuable", probe_continuable)
    router.register("txt_to_epub.read_artifacts", read_artifacts)
    router.register("txt_to_epub.read_report", read_report)
    router.register("txt_to_epub.list_failed_subtasks", list_failed_subtasks)


def _options(payload: Mapping[str, object]) -> TxtToEpubOptions:
    raw_options = payload.get("options", payload)
    if not isinstance(raw_options, Mapping):
        raise BridgeError.invalid_argument(
            "options must be an object.",
            field="options",
        )
    return TxtToEpubOptions.from_mapping(raw_options)


__all__ = ["register"]
