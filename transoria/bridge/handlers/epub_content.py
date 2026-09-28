from __future__ import annotations

import zipfile
from pathlib import Path
from threading import RLock
from typing import Mapping

from lxml import etree

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.tools.epub_content import ContentSessionStore


def _paths(payload: Mapping[str, object]) -> list[str]:
    value = payload.get("paths")
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        raise ValueError("paths must be a list of EPUB resource paths.")
    return value


def _entries(payload: Mapping[str, object]) -> list[dict[str, object]]:
    value = payload.get("entries")
    if not isinstance(value, list) or not all(
        isinstance(entry, dict) for entry in value
    ):
        raise ValueError("entries must be a list of table-of-contents entries.")
    return value


def register(router: BridgeRouter, *, cache_root: Path | None = None) -> None:
    store = ContentSessionStore(cache_root / "epub-editor-sessions" if cache_root else None)
    lock = RLock()

    def run(payload: Mapping[str, object], action: str) -> dict[str, object]:
        try:
            if action == "open":
                return store.open(expect_string(payload, "input_path"))
            session_id = expect_string(payload, "session_id")
            if action == "close":
                store.close(session_id)
                return {"closed": True}
            session = store.get(session_id)
            if action == "info":
                return session.info(session_id)
            if action == "read":
                return session.read(expect_string(payload, "path"))
            if action == "write":
                session.write(
                    expect_string(payload, "path"),
                    expect_string(payload, "content", allow_empty=True),
                )
            elif action == "search":
                return {
                    "matches": session.search(
                        expect_string(payload, "query", allow_empty=True),
                        _paths(payload),
                        bool(payload.get("case_sensitive", False)),
                        bool(payload.get("regular_expression", False)),
                    )
                }
            elif action == "replace":
                expected_count = payload.get("expected_count")
                if expected_count is not None and (
                    not isinstance(expected_count, int) or expected_count < 0
                ):
                    raise ValueError("expected_count must be a non-negative integer.")
                result = session.replace(
                    expect_string(payload, "query"),
                    expect_string(payload, "replacement", allow_empty=True),
                    _paths(payload),
                    bool(payload.get("case_sensitive", False)),
                    expected_count,
                    bool(payload.get("regular_expression", False)),
                )
                store.persist(session_id)
                return {**result, **session.info(session_id)}
            elif action == "replace_match":
                start = payload.get("start")
                end = payload.get("end")
                if not isinstance(start, int) or not isinstance(end, int):
                    raise ValueError("Match offsets must be integers.")
                session.replace_match(
                    expect_string(payload, "path"),
                    start,
                    end,
                    expect_string(payload, "query"),
                    expect_string(payload, "replacement", allow_empty=True),
                    bool(payload.get("case_sensitive", False)),
                    bool(payload.get("regular_expression", False)),
                )
            elif action == "reorder_spine":
                session.reorder_spine(_paths(payload))
            elif action == "set_toc":
                session.set_toc(_entries(payload))
            elif action == "generate_toc":
                summary = session.generate_toc()
                store.persist(session_id)
                return {**summary, **session.info(session_id)}
            elif action in {"undo", "redo"}:
                session.history(action)
            elif action == "preview":
                return {"html": session.preview(expect_string(payload, "path"))}
            elif action == "preview_draft":
                return {
                    "html": session.preview(
                        expect_string(payload, "path"),
                        expect_string(payload, "draft_path"),
                        expect_string(payload, "draft_content", allow_empty=True),
                    )
                }
            elif action == "validate":
                return session.validate()
            elif action == "save":
                result = session.save(
                    expect_string(payload, "output_path"),
                    bool(payload.get("overwrite", False)),
                )
                store.persist(session_id)
                return {**result, **session.info(session_id)}
            else:
                raise ValueError("Unknown EPUB content editor action.")
            if action in {"write", "replace_match", "reorder_spine", "set_toc", "undo", "redo"}:
                store.persist(session_id)
            return session.info(session_id)
        except (
            FileNotFoundError,
            KeyError,
            ValueError,
            OSError,
            zipfile.BadZipFile,
            etree.XMLSyntaxError,
        ) as exc:
            raise BridgeError.invalid_argument(str(exc)) from exc

    for action in (
        "open",
        "close",
        "info",
        "read",
        "write",
        "search",
        "replace",
        "replace_match",
        "reorder_spine",
        "set_toc",
        "generate_toc",
        "undo",
        "redo",
        "preview",
        "preview_draft",
        "validate",
        "save",
    ):
        def locked_run(payload: Mapping[str, object], action: str = action) -> dict[str, object]:
            with lock:
                return run(payload, action)

        router.register(
            f"epub_content.{action}",
            locked_run,
        )
