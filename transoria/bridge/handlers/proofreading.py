"""Proofreading bridge handlers.

Reads cached translation tasks for the校对 page so the user can fix up
suspect translations and regenerate the output files. The cache is the
source of truth — every edit writes back to ``<cache_root>/tasks/<task_id>/
subtasks/<id>.json`` synchronously, and ``regenerate_outputs`` walks the
cache to overwrite the original ``.epub`` / ``.txt`` deliverables in
place.

Editing does NOT call the LLM. ``retranslate_segment`` (deferred to a
later iteration) will, but a regular edit is a deterministic JSON patch.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.bridge.task_service import (
    TaskService,
    _confidence_entry_for_segment,
    _replace_low_confidence_entry,
)
from transoria.domain import Language, TaskKind, TaskStatus
from transoria.runtime.cache import TaskNotFoundError


def _segment_sort_key(segment_id: str) -> tuple[int, int]:
    """Sort items by ``(file_index, segment_index)`` so the校对 table
    follows the original chapter order across all source files."""

    parts = segment_id.split(":", 1)
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _decode_response(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _collect_translations_from_cache(snapshot) -> dict[str, str]:
    """Walk every subtask and union all ``translations`` maps. Later
    subtasks (split children) override earlier ones because the
    orchestrator's split path leaves the parent in SKIPPED with stale
    translations and the children carry the authoritative output."""

    translations: dict[str, str] = {}
    for subtask in snapshot.subtasks:
        payload = _decode_response(subtask.response_content or "")
        records = payload.get("translations", {})
        if isinstance(records, dict):
            for seg_id, text in records.items():
                translations[str(seg_id)] = str(text)
    return translations


def _build_handlers(service: TaskService) -> dict[str, object]:
    def require_completed_translation_task(task_id: str):
        try:
            snapshot = service.cache.load(task_id)
        except TaskNotFoundError as exc:
            raise BridgeError.not_found(
                f"task {task_id!r} not found in cache.",
                details={"task_id": task_id},
            ) from exc
        if snapshot.record.kind is not TaskKind.TRANSLATION:
            raise BridgeError.invalid_argument(
                f"task {task_id!r} is not a translation task.",
                details={"task_id": task_id},
            )
        if snapshot.record.status is not TaskStatus.COMPLETED:
            raise BridgeError.conflict(
                "proofreading is only available after translation completes.",
                details={"task_id": task_id, "status": snapshot.record.status.value},
            )
        return snapshot

    def list_tasks(_payload: Mapping[str, object]) -> dict[str, object]:
        listing = service.list_recent_tasks(kind="translation", limit=None)
        out: list[dict[str, object]] = []
        for header in listing["tasks"]:
            task_id = header.get("id")
            if not isinstance(task_id, str):
                continue
            # Only surface tasks that actually have at least one
            # subtask in cache — runs that crashed before any subtask
            # was seeded have nothing to proofread.
            try:
                snapshot = service.cache.load(task_id)
            except (TaskNotFoundError, OSError, ValueError):
                continue
            if snapshot.record.status is not TaskStatus.COMPLETED:
                continue
            if not snapshot.subtasks:
                continue
            out.append(header)
        return {"tasks": out}

    def load_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        task_id = expect_string(payload, "task_id")
        snapshot = require_completed_translation_task(task_id)

        # Aggregate per-segment data across subtasks. Latest write wins
        # (split children override the parent) so edits via update_segment
        # always read back consistently.
        translations = _collect_translations_from_cache(snapshot)
        low_conf_ids: set[str] = set()
        seg_tags: dict[str, list[str]] = {}
        seg_reasons: dict[str, list[str]] = {}
        for subtask in snapshot.subtasks:
            resp = _decode_response(subtask.response_content or "")
            entries = resp.get("low_confidence", [])
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict):
                        sid = entry.get("segment_id")
                        if isinstance(sid, str):
                            low_conf_ids.add(sid)
                            reasons = entry.get("reasons", [])
                            if isinstance(reasons, list):
                                merged_reasons = seg_reasons.setdefault(sid, [])
                                for reason in reasons:
                                    if (
                                        isinstance(reason, str)
                                        and reason not in merged_reasons
                                    ):
                                        merged_reasons.append(reason)
                            tags = entry.get("tags", [])
                            if isinstance(tags, list):
                                merged = seg_tags.setdefault(sid, [])
                                for t in tags:
                                    if isinstance(t, str) and t not in merged:
                                        merged.append(t)

        # Build (segment_id, src) map. Each segment appears exactly once
        # in the parent subtask's request_payload, but split children
        # repeat it. Use the first occurrence for source text — they all
        # share the same `original_text` slot anyway.
        seen: dict[str, dict[str, object]] = {}
        for subtask in snapshot.subtasks:
            req = subtask.request_payload or {}
            segments = req.get("segments", [])
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                seg_id = segment.get("segment_id")
                if not isinstance(seg_id, str):
                    continue
                if seg_id in seen:
                    continue
                src = str(segment.get("original_text", ""))
                dst = translations.get(seg_id)
                if dst is None:
                    dst = src
                    low_conf_ids.add(seg_id)
                    reasons = seg_reasons.setdefault(seg_id, [])
                    if "missing_translation_fell_back_to_source" not in reasons:
                        reasons.append("missing_translation_fell_back_to_source")
                    tags = seg_tags.setdefault(seg_id, [])
                    if "source_residue" not in tags:
                        tags.append("source_residue")
                item: dict[str, object] = {
                    "segment_id": seg_id,
                    "src": src,
                    "dst": dst,
                    "low_confidence": seg_id in low_conf_ids,
                }
                tags_for_seg = seg_tags.get(seg_id)
                if tags_for_seg:
                    item["tags"] = tags_for_seg
                reasons_for_seg = seg_reasons.get(seg_id)
                if reasons_for_seg:
                    item["reasons"] = reasons_for_seg
                seen[seg_id] = item

        items = sorted(seen.values(), key=lambda i: _segment_sort_key(str(i["segment_id"])))
        return {
            "task_id": task_id,
            "task_status": snapshot.record.status.value,
            "input_dir": str(snapshot.record.metadata.get("input_dir", "")),
            "output_dir": str(snapshot.record.metadata.get("output_dir", "")),
            "items": items,
        }

    def update_segment(payload: Mapping[str, object]) -> dict[str, object]:
        task_id = expect_string(payload, "task_id")
        segment_id = expect_string(payload, "segment_id")
        new_dst_raw = payload.get("dst")
        if not isinstance(new_dst_raw, str):
            raise BridgeError.invalid_argument(
                "dst must be a string.",
                field="dst",
            )
        snapshot = require_completed_translation_task(task_id)

        # Find every subtask whose request_payload owns this segment_id.
        # When a parent has been split, both the (SKIPPED) parent and
        # one of the (COMPLETED) children carry the segment — write to
        # all of them so the next load_snapshot reads back the new dst
        # regardless of which subtask the union picks first.
        owners = []
        for subtask in snapshot.subtasks:
            req = subtask.request_payload or {}
            segments = req.get("segments", [])
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if isinstance(segment, dict) and segment.get("segment_id") == segment_id:
                    owners.append(subtask)
                    break
        if not owners:
            raise BridgeError.not_found(
                f"segment {segment_id!r} not found in task {task_id!r}.",
                details={"task_id": task_id, "segment_id": segment_id},
            )

        confidence_entry = _confidence_entry_for_segment(
            snapshot, segment_id, new_dst_raw
        )
        for subtask in owners:
            current = _decode_response(subtask.response_content or "")
            current.setdefault("version", 2)
            translations = current.setdefault("translations", {})
            if not isinstance(translations, dict):
                translations = {}
                current["translations"] = translations
            translations[segment_id] = new_dst_raw
            _replace_low_confidence_entry(current, segment_id, confidence_entry)
            service.cache.save_subtask(
                replace(
                    subtask,
                    response_content=json.dumps(current, ensure_ascii=False),
                )
            )
        tags = []
        reasons = []
        if confidence_entry is not None:
            raw_reasons = confidence_entry.get("reasons")
            if isinstance(raw_reasons, list):
                reasons = [str(reason) for reason in raw_reasons]
            raw_tags = confidence_entry.get("tags")
            if isinstance(raw_tags, list):
                tags = [str(tag) for tag in raw_tags]
        return {
            "updated": True,
            "segment_id": segment_id,
            "dst": new_dst_raw,
            "low_confidence": confidence_entry is not None,
            "tags": tags,
            "reasons": reasons,
        }

    def regenerate_outputs(
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        # Lazy import to avoid pulling the workflow stack into the bridge
        # entry point at module load time.
        from transoria.workflows.translation.config import (  # noqa: PLC0415
            TranslationConfig,
        )
        from transoria.workflows.translation.orchestrator import (  # noqa: PLC0415
            _prepare_segments,
            _scan_and_parse,
            _write_outputs,
        )
        from transoria.workflows.translation.rules import Glossary  # noqa: PLC0415

        task_id = expect_string(payload, "task_id")
        export_bilingual = bool(payload.get("bilingual", False))
        snapshot = require_completed_translation_task(task_id)

        # Pull the original folder pair + language from the cache
        # record, not the current settings — proofreading is decoupled
        # from "what folder is the user now staring at".
        record_metadata = snapshot.record.metadata
        input_dir_str = str(record_metadata.get("input_dir", ""))
        output_dir_str = str(record_metadata.get("output_dir", ""))
        if not input_dir_str or not output_dir_str:
            raise BridgeError.invalid_argument(
                "task cache is missing input/output folder metadata; cannot regenerate.",
                details={"task_id": task_id},
            )

        input_dir = Path(input_dir_str)
        if not input_dir.exists():
            raise BridgeError.not_found(
                f"input folder {input_dir_str!r} no longer exists.",
                details={"task_id": task_id, "input_dir": input_dir_str},
            )

        # Build a minimal TranslationConfig sufficient for the writer.
        # No model / prompt / rules are needed because regen does not
        # call the LLM and the cached translations are already past the
        # postprocess (post_replacement) stage.
        try:
            source_language = Language(
                str(record_metadata.get("source_language", Language.KOREAN.value))
            )
            target_language = Language(
                str(
                    record_metadata.get(
                        "target_language", Language.CHINESE_SIMPLIFIED.value
                    )
                )
            )
        except ValueError as exc:
            raise BridgeError.invalid_argument(
                f"task cache has invalid language metadata: {exc}",
                details={"task_id": task_id},
            ) from exc

        # ``_prepare_segments`` runs the preprocessor, which uses
        # text_preserve_rules / pre_replacements only to mask + rewrite
        # the prompt text. For regen the prompt text is irrelevant —
        # only the segment_id list matters, and segment_ids are
        # ``"<file_index>:<segment_index>"`` from raw parsing, stable
        # regardless of rules. Empty rules are fine here.
        config = TranslationConfig(
            input_dir=input_dir,
            output_dir=Path(output_dir_str),
            source_language=source_language,
            target_language=target_language,
            model=None,  # type: ignore[arg-type]
            prompt_preset=None,  # type: ignore[arg-type]
            glossary=Glossary.empty(),
            bilingual_enabled=export_bilingual,
        )

        try:
            parsed_files = _scan_and_parse(
                config.input_dir, buffer_epub_archives=False
            )
        except FileNotFoundError as exc:
            raise BridgeError.not_found(
                f"input folder {input_dir_str!r} no longer exists.",
                details={"task_id": task_id, "input_dir": input_dir_str},
            ) from exc
        if not parsed_files:
            raise BridgeError.invalid_argument(
                f"input folder {input_dir_str!r} contains no .epub or .txt files; "
                "the original source may have been moved or renamed.",
                details={"task_id": task_id, "input_dir": input_dir_str},
            )
        _flat, prepared_per_file = _prepare_segments(parsed_files, config)

        translations_by_segment = _collect_translations_from_cache(snapshot)
        try:
            translated, bilingual, failed = _write_outputs(
                parsed_files,
                translations_by_segment,
                prepared_per_file,
                config,
            )
        except OSError as exc:
            raise BridgeError(
                "bridge.io_error",
                f"failed to write regenerated outputs: {exc}",
                retryable=True,
                details={"task_id": task_id},
            ) from exc

        return {
            "task_id": task_id,
            "translated_files": [str(p) for p in translated],
            "bilingual_files": [str(p) for p in bilingual],
            "failed_files": [
                {
                    "path": item.path,
                    "reason": item.reason,
                    "code": item.code,
                    "details": item.details,
                }
                for item in failed
            ],
        }

    def retranslate_segment(payload: Mapping[str, object]) -> dict[str, object]:
        return service.start_retranslate(
            task_id=expect_string(payload, "task_id"),
            segment_id=expect_string(payload, "segment_id"),
        )

    def retranslate_status(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_retranslate_status(
            request_id=expect_string(payload, "request_id"),
        )

    return {
        "proofreading.list_tasks": list_tasks,
        "proofreading.load_snapshot": load_snapshot,
        "proofreading.update_segment": update_segment,
        "proofreading.regenerate_outputs": regenerate_outputs,
        "proofreading.retranslate_segment": retranslate_segment,
        "proofreading.retranslate_status": retranslate_status,
    }


def register(router: BridgeRouter, *, service: TaskService) -> None:
    for method, handler in _build_handlers(service).items():
        router.register(method, handler)


__all__ = ["register"]
