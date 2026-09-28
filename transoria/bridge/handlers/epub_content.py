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


def _selection(payload: Mapping[str, object]) -> dict[str, object] | None:
    value = payload.get("selection")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("selection must describe an EPUB text range.")
    return value


def _fingerprints(payload: Mapping[str, object]) -> dict[str, str] | None:
    value = payload.get("expected_fingerprints")
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(path, str) and isinstance(digest, str) for path, digest in value.items()
    ):
        raise ValueError("expected_fingerprints must map resource paths to hashes.")
    return value


def _patterns(payload: Mapping[str, object]) -> list[str] | None:
    value = payload.get("patterns")
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("patterns must be a list of directory level expressions.")
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
            if action == "checkpoint":
                store.persist(session_id)
                return session.info(session_id)
            if action == "read":
                return session.read(expect_string(payload, "path"))
            if action == "anchors":
                return {"anchors": session.anchors(expect_string(payload, "path"))}
            if action == "references":
                return {"inbound": session.resource_references(expect_string(payload, "path"))}
            if action == "export_resource":
                return {"output_path": session.export_resource(
                    expect_string(payload, "path"),
                    expect_string(payload, "output_path"),
                    bool(payload.get("overwrite", False)),
                )}
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
                        _selection(payload),
                    )
                }
            elif action == "preview_replace":
                return session.preview_replace(
                    expect_string(payload, "query"),
                    expect_string(payload, "replacement", allow_empty=True),
                    _paths(payload),
                    bool(payload.get("case_sensitive", False)),
                    bool(payload.get("regular_expression", False)),
                    _selection(payload),
                )
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
                    _selection(payload),
                    _fingerprints(payload),
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
            elif action == "set_spine":
                entries = payload.get("entries")
                if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
                    raise ValueError("entries must be a list of reading-order items.")
                session.set_spine(entries)
            elif action == "set_toc":
                session.set_toc(_entries(payload))
            elif action == "generate_toc":
                summary = session.generate_toc(_patterns(payload))
                store.persist(session_id)
                return {**summary, **session.info(session_id)}
            elif action == "preview_toc":
                return session.generate_toc(_patterns(payload), preview_only=True)
            elif action == "generate_toc_page":
                path = session.generate_toc_page(expect_string(payload, "title"))
                store.persist(session_id)
                return {"generated_path": path, **session.info(session_id)}
            elif action in {"add_resource", "replace_resource"}:
                input_path = Path(expect_string(payload, "input_path")).expanduser().resolve()
                if not input_path.is_file() or input_path.stat().st_size > 48_000_000:
                    raise ValueError("Select a local resource under 48 MB.")
                data = input_path.read_bytes()
                if action == "add_resource":
                    session.add_resource(
                        expect_string(payload, "path"), data,
                        expect_string(payload, "media_type", allow_empty=True) if "media_type" in payload else "",
                        bool(payload.get("in_spine", False)),
                    )
                else:
                    session.replace_resource(expect_string(payload, "path"), data)
            elif action == "rename_resource":
                summary = session.rename_resource(
                    expect_string(payload, "path"), expect_string(payload, "target"),
                )
                store.persist(session_id)
                return {**summary, **session.info(session_id)}
            elif action == "delete_resource":
                session.delete_resource(expect_string(payload, "path"))
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
            if action in {"write", "replace_match", "reorder_spine", "set_spine", "set_toc", "add_resource", "replace_resource", "delete_resource", "undo", "redo"}:
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
        "checkpoint",
        "read",
        "anchors",
        "references",
        "export_resource",
        "write",
        "search",
        "preview_replace",
        "replace",
        "replace_match",
        "reorder_spine",
        "set_spine",
        "set_toc",
        "generate_toc",
        "preview_toc",
        "generate_toc_page",
        "add_resource",
        "replace_resource",
        "rename_resource",
        "delete_resource",
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
