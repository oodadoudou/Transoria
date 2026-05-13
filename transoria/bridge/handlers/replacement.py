"""``replacement.*`` bridge handlers.

The rule-parsing endpoints (``import_rules`` / ``validate_rules``) are pure
parsers; they live here because the contract groups them under the same
domain. The task-lifecycle methods (``start_task``/``stop_task``/...) are
backed by :class:`TaskService` and registered through :func:`register_tasks`
to keep their dependency surface explicit.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.bridge.task_service import TaskService
from transoria.formats.text import decode_text_bytes, parse_txt_file
from transoria.tools.replacement import ReplacementRule


def _parse_rule_line(
    line: str, line_number: int
) -> tuple[dict[str, object] | None, str | None]:
    stripped = line.strip()
    if not stripped:
        return None, None
    # Comment lines start with `#` AND have no rule separator. A real
    # rule line that happens to begin with `#` (some community formats
    # use leading `#` as a context anchor on the source phrase) is not
    # a comment and must still parse.
    if stripped.startswith("#") and "->" not in stripped:
        return None, None
    if "->" not in stripped:
        return None, f"line {line_number}: missing '->' separator"
    src, _, dst = stripped.partition("->")
    src = src.strip()
    dst = dst.strip()
    # Some replacement-rule conventions wrap the phrase in ``#`` as a
    # context anchor (``src#->#dst``); these markers are not part of
    # the actual text and must be stripped before the rule reaches
    # ``apply_rules``, otherwise the literal ``#`` would never match
    # in the user's source text. Only strip when both sides carry the
    # marker — a one-sided ``#`` is more likely intentional content.
    if src.endswith("#") and dst.startswith("#"):
        src = src[:-1].rstrip()
        dst = dst[1:].lstrip()
    if not src:
        return None, f"line {line_number}: empty source phrase"
    return (
        {
            "src": src,
            "dst": dst,
            # Imported rules default to literal exact match — users
            # writing ``original->replacement`` lines expect the source
            # phrase to match verbatim, not as a regex. Anyone who
            # wants regex / case-insensitive can flip those flags per
            # rule afterwards in the UI.
            "regex": False,
            "case_sensitive": True,
            "enabled": True,
        },
        None,
    )


_RED_HEADER = b"RED\x01"
_RED_RULE_FIELDS = ("rule", "替换原文", "原文", "src", "source", "from", "pattern")
_RED_TARGET_FIELDS = ("target", "替换后", "替换为", "dst", "to", "replacement")


def _first_string(payload: Mapping[str, object], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _load_red_json(path: Path) -> object:
    raw = path.read_bytes()
    if raw.startswith(_RED_HEADER):
        raw = raw[len(_RED_HEADER) :]
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    text, _encoding = decode_text_bytes(raw)
    return json.loads(text)


def _iter_red_items(payload: object) -> Sequence[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in ("data", "rules", "items"):
            items = payload.get(key)
            if isinstance(items, list):
                return items
    return ()


def _parse_red_rules(path: Path) -> dict[str, object]:
    try:
        payload = _load_red_json(path)
    except (
        OSError,
        UnicodeDecodeError,
        gzip.BadGzipFile,
        json.JSONDecodeError,
    ) as exc:
        raise BridgeError(
            "bridge.io_error",
            f"cannot read RED rule file: {exc}",
            retryable=isinstance(exc, OSError),
            details={"path": str(path)},
        ) from exc

    rules: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    for index, item in enumerate(_iter_red_items(payload), start=1):
        if not isinstance(item, Mapping):
            warnings.append(
                {"line_number": index, "message": "rule item is not an object"}
            )
            continue
        src = (_first_string(item, _RED_RULE_FIELDS) or "").strip()
        dst = (_first_string(item, _RED_TARGET_FIELDS) or "").strip()
        if not src:
            warnings.append({"line_number": index, "message": "empty source phrase"})
            continue
        rules.append(
            {
                "src": src,
                "dst": dst,
                "regex": bool(item.get("isRegex", item.get("regex", False))),
                "case_sensitive": True,
                "enabled": bool(item.get("enabled", True)),
            }
        )
    if not rules and not warnings:
        warnings.append({"line_number": 0, "message": "missing RED rule list"})
    return {"rules": rules, "parse_warnings": warnings}


def import_rules(payload: Mapping[str, object]) -> dict[str, object]:
    path_str = expect_string(payload, "path")
    path = Path(path_str)
    if not path.exists():
        raise BridgeError.not_found(
            f"rule file does not exist: {path_str!r}",
            details={"path": path_str},
        )
    if path.suffix.casefold() == ".red":
        return _parse_red_rules(path)
    # Reuse the txt parser's tolerant detection (utf-8 → BOM-checked
    # utf-16 → chardet → cp949/euc-kr/gbk/big5/shift_jis fallback) so
    # users can drop legacy-encoded rule files without converting first.
    try:
        document = parse_txt_file(path)
    except UnicodeDecodeError as exc:
        raise BridgeError(
            "bridge.io_error",
            f"cannot decode rule file: {exc}",
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
    for index, segment in enumerate(document.segments, start=1):
        rule, warning = _parse_rule_line(segment.text, index)
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
        input_folder = payload.get("input_folder")
        output_folder = payload.get("output_folder")
        if input_folder is not None and not isinstance(input_folder, str):
            raise BridgeError.invalid_argument(
                "input_folder must be a string.",
                field="input_folder",
            )
        if output_folder is not None and not isinstance(output_folder, str):
            raise BridgeError.invalid_argument(
                "output_folder must be a string.",
                field="output_folder",
            )
        return service.start_replacement(
            request_id=request_id,
            rules=rules,
            input_folder=input_folder,
            output_folder=output_folder,
        )

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

    def read_replacement_report(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_replacement_report(
            task_id=expect_string(payload, "task_id")
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
    router.register("replacement.read_replacement_report", read_replacement_report)


__all__ = [
    "import_rules",
    "register_parsers",
    "register_tasks",
    "validate_rules",
]
