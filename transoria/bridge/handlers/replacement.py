"""``replacement.*`` bridge handlers.

Step 7 ships rule import/validate as live; the actual replacement task
runner is deferred to a follow-up that wires the existing
:mod:`transoria.tools.replacement` engine through the runtime executor.
For v1 the start/stop/snapshot methods raise typed errors so the UI can
disable the Execute button cleanly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter


def _parse_rule_line(line: str, line_number: int) -> tuple[dict[str, object] | None, str | None]:
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


def _start_task_unsupported(_payload: Mapping[str, object]) -> dict[str, object]:
    raise BridgeError.invalid_argument(
        "replacement.start_task is not yet implemented.",
        details={"reason": "unsupported"},
    )


def _stop_task_unsupported(_payload: Mapping[str, object]) -> dict[str, object]:
    raise BridgeError.invalid_argument(
        "replacement.stop_task is not yet implemented.",
        details={"reason": "unsupported"},
    )


def _read_artifacts_unsupported(_payload: Mapping[str, object]) -> dict[str, object]:
    raise BridgeError.invalid_argument(
        "replacement.read_artifacts is not yet implemented.",
        details={"reason": "unsupported"},
    )


def register(router: BridgeRouter) -> None:
    router.register("replacement.import_rules", import_rules)
    router.register("replacement.validate_rules", validate_rules)
    router.register("replacement.start_task", _start_task_unsupported)
    router.register("replacement.stop_task", _stop_task_unsupported)
    router.register("replacement.read_artifacts", _read_artifacts_unsupported)


__all__ = ["import_rules", "register", "validate_rules"]
