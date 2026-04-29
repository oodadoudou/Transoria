"""``replacement.*`` bridge handlers.

The rule-parsing endpoints (``import_rules`` / ``validate_rules``) are pure
parsers; they live here because the contract groups them under the same
domain. The task-lifecycle methods (``start_task``/``stop_task``/...) are
backed by :class:`TaskService` and registered through :func:`register_tasks`
to keep their dependency surface explicit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.bridge.task_service import TaskService
from transoria.tools.replacement import ReplacementRule


def _parse_rule_line(
    line: str, line_number: int
) -> tuple[dict[str, object] | None, str | None]:
    stripped = line.strip()
    if not stripped:
        return None, None
    if stripped.startswith("#"):
        return None, None
    if "->" not in stripped:
        return None, f"line {line_number}: missing '->' separator"
    src, _, dst = stripped.partition("->")
    src = src.strip()
    dst = dst.strip()
    if not src:
        return None, f"line {line_number}: empty source phrase"
    return (
        {
            "src": src,
            "dst": dst,
            "regex": False,
            "case_sensitive": False,
            "enabled": True,
        },
        None,
    )


def import_rules(payload: Mapping[str, object]) -> dict[str, object]:
    path_str = expect_string(payload, "path")
    path = Path(path_str)
    if not path.exists():
        raise BridgeError.not_found(
            f"rule file does not exist: {path_str!r}",
            details={"path": path_str},
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BridgeError(
                "bridge.io_error",
                f"cannot decode rule file as UTF-8: {exc}",
                retryable=False,
                details={"path": path_str},
            ) from exc
    except OSError as exc:
        raise BridgeError(
            "bridge.io_error",
            f"cannot read rule file: {exc}",
            retryable=True,
            details={"path": path_str},
        ) from exc

    rules: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        rule, warning = _parse_rule_line(line, index)
        if rule is not None:
            rules.append(rule)
        if warning is not None:
            warnings.append({"line_number": index, "message": warning})
    return {"rules": rules, "parse_warnings": warnings}


def validate_rules(payload: Mapping[str, object]) -> dict[str, object]:
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise BridgeError.invalid_argument(
            "rules must be a list.",
            field="rules",
        )
    issues: list[dict[str, object]] = []
    seen: dict[str, int] = {}
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            issues.append(
                {
                    "rule_index": index,
                    "code": "empty_src",
                    "message": "rule must be an object",
                }
            )
            continue
        src = rule.get("src")
        dst = rule.get("dst")
        regex = bool(rule.get("regex", False))
        if not isinstance(src, str) or not src:
            issues.append(
                {
                    "rule_index": index,
                    "code": "empty_src",
                    "message": "src is empty",
                }
            )
            continue
        if not isinstance(dst, str):
            issues.append(
                {
                    "rule_index": index,
                    "code": "empty_dst",
                    "message": "dst must be a string",
                }
            )
            continue
        if regex:
            try:
                re.compile(src)
            except re.error as exc:
                issues.append(
                    {
                        "rule_index": index,
                        "code": "regex_error",
                        "message": f"invalid regex: {exc}",
                    }
                )
                continue
        if src in seen:
            issues.append(
                {
                    "rule_index": index,
                    "code": "duplicate_src",
                    "message": f"duplicate of rule #{seen[src]}",
                }
            )
            continue
        seen[src] = index
    return {"ok": not issues, "issues": issues}


def _coerce_rules(raw: Sequence[Mapping[str, object]]) -> tuple[ReplacementRule, ...]:
    coerced: list[ReplacementRule] = []
    for index, rule in enumerate(raw):
        if not isinstance(rule, Mapping):
            raise BridgeError.invalid_argument(
                f"rules[{index}] must be an object.",
                field="rules",
            )
        src = rule.get("src")
        dst = rule.get("dst", "")
        if not isinstance(src, str) or not src:
            raise BridgeError.invalid_argument(
                f"rules[{index}].src is required.",
                field="rules",
            )
        if not isinstance(dst, str):
            raise BridgeError.invalid_argument(
                f"rules[{index}].dst must be a string.",
                field="rules",
            )
        coerced.append(
            ReplacementRule(
                src=src,
                dst=dst,
                regex=bool(rule.get("regex", False)),
                case_sensitive=bool(rule.get("case_sensitive", False)),
                enabled=bool(rule.get("enabled", True)),
            )
        )
    return tuple(coerced)


def register_parsers(router: BridgeRouter) -> None:
    """Register only the rule parsing endpoints (no task lifecycle).

    The production router calls this first, then :func:`register_tasks`
    with a live :class:`TaskService`. Tests that only need to exercise
    ``import_rules`` / ``validate_rules`` can stop here.
    """

    router.register("replacement.import_rules", import_rules)
    router.register("replacement.validate_rules", validate_rules)


def register_tasks(router: BridgeRouter, *, service: TaskService) -> None:
    """Register the live replacement task lifecycle handlers.

    Must be called after :func:`register_parsers` and exactly once per
    router. Lifecycle methods are independent from parser methods, so
    public ``router.register`` is sufficient — no override.
    """

    def start_task(payload: Mapping[str, object]) -> dict[str, object]:
        request_id = expect_string(payload, "request_id")
        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, list):
            raise BridgeError.invalid_argument(
                "rules must be a list.",
                field="rules",
            )
        rules = _coerce_rules(raw_rules)
        return service.start_replacement(request_id=request_id, rules=rules)

    def stop_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.stop_task(
            kind="replacement", task_id=expect_string(payload, "task_id")
        )

    def pause_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.pause_task(
            kind="replacement", task_id=expect_string(payload, "task_id")
        )

    def continue_task(payload: Mapping[str, object]) -> dict[str, object]:
        return service.continue_task(
            kind="replacement", task_id=expect_string(payload, "task_id")
        )

    def probe_continuable(_payload: Mapping[str, object]) -> dict[str, object]:
        return service.probe_continuable(kind="replacement")

    def read_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_snapshot(
            kind="replacement", task_id=expect_string(payload, "task_id")
        )

    def list_failed_subtasks(payload: Mapping[str, object]) -> dict[str, object]:
        return service.list_failed_subtasks(
            kind="replacement", task_id=expect_string(payload, "task_id")
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
        return service.list_recent_tasks(kind="replacement", limit=limit)

    def read_artifacts(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_artifacts(
            kind="replacement", task_id=expect_string(payload, "task_id")
        )

    router.register("replacement.start_task", start_task)
    router.register("replacement.stop_task", stop_task)
    router.register("replacement.pause_task", pause_task)
    router.register("replacement.continue_task", continue_task)
    router.register("replacement.probe_continuable", probe_continuable)
    router.register("replacement.read_snapshot", read_snapshot)
    router.register("replacement.list_failed_subtasks", list_failed_subtasks)
    router.register("replacement.list_recent_tasks", list_recent_tasks)
    router.register("replacement.read_artifacts", read_artifacts)


__all__ = [
    "import_rules",
    "register_parsers",
    "register_tasks",
    "validate_rules",
]
