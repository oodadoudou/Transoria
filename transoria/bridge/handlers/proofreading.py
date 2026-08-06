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

import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import replace
from difflib import SequenceMatcher
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
from transoria.domain import Language, SubtaskStatus, TaskKind, TaskStatus
from transoria.runtime.cache import TaskNotFoundError
from transoria.workflows.translation import evaluate_segment_confidence
from transoria.workflows.translation.glossary_report import target_term_present
from transoria.workflows.translation.rules import Glossary, GlossaryEntry
from transoria.workflows.translation.segment_state import (
    accepted_override_payload,
    collect_segment_state_from_payloads,
    mark_accepted_override,
)

_PROOFREADABLE_TRANSLATION_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED}
)
_POSSIBLE_DUPLICATE_TAG = "possible_duplicate"
_POSSIBLE_DUPLICATE_REASON = "adjacent_translation_possible_duplicate"
_TERM_INCONSISTENCY_TAG = "term_inconsistency"
_TERM_INCONSISTENCY_REASON = "glossary_term_translation_inconsistent"
_GLOSSARY_NOT_APPLIED_TAG = "glossary_not_applied"
_GLOSSARY_NOT_APPLIED_REASON = "glossary_term_target_missing"
_DUPLICATE_SCAN_MIN_TRANSLATION_LENGTH = 4
_DUPLICATE_SCAN_WINDOW = 3
_DUPLICATE_SCAN_TRANSLATION_RATIO = 0.92
_DUPLICATE_SCAN_OVERLAP_RATIO = 0.88
_DUPLICATE_SCAN_SHORT_TRANSLATION_RATIO = 0.98
_DUPLICATE_SCAN_SHORT_TEXT_LENGTH = 16
_DUPLICATE_SCAN_SHORT_SOURCE_RATIO = 0.35
_DUPLICATE_SCAN_SOURCE_RATIO = 0.56
_DUPLICATE_SCAN_MIN_DELTA = 0.32
_DUPLICATE_SCAN_TAGGED_PARTNER_RATIO = 0.74
_DUPLICATE_SCAN_TAGGED_PARTNER_OVERLAP = 0.74
_TERM_AUDIT_VERSION = "canonical-missing-v2"
_TERM_AUDIT_CACHE_MAX_TASKS = 8
_TEXT_SIMILARITY_NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _glossary_entry_key(entry: GlossaryEntry) -> tuple[str, str, str, bool, bool]:
    return (
        entry.src,
        entry.dst,
        entry.info,
        entry.regex,
        entry.case_sensitive,
    )


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise BridgeError.invalid_argument(
            f"{key} must be a string.",
            field=key,
        )
    return value


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


def _decoded_subtask_responses(snapshot) -> list[tuple[Any, dict[str, Any]]]:
    return [
        (subtask, _decode_response(subtask.response_content or ""))
        for subtask in snapshot.subtasks
    ]


def _collect_segment_state_from_responses(
    decoded_responses: list[tuple[Any, dict[str, Any]]],
) -> tuple[dict[str, str], dict[str, dict[str, list[str]]]]:
    payloads: list[Mapping[str, object]] = []
    for subtask, payload in decoded_responses:
        if getattr(subtask, "status", None) is SubtaskStatus.COMPLETED:
            payloads.append(payload)
            continue
        accepted_payload = accepted_override_payload(payload)
        if accepted_payload is not None:
            payloads.append(accepted_payload)
    return collect_segment_state_from_payloads(payloads)


def _collect_translations_from_responses(
    decoded_responses: list[tuple[Any, dict[str, Any]]],
) -> dict[str, str]:
    translations, _low_confidence = _collect_segment_state_from_responses(
        decoded_responses
    )
    return translations


def _collect_translations_from_cache(snapshot) -> dict[str, str]:
    """Collect accepted translations from authoritative cached subtasks.

    Failed/running/skipped subtasks may retain old response payloads from
    retries or split parents. Those payloads are diagnostic unless a segment
    was explicitly accepted by manual proofreading or single-row retranslation.
    """

    return _collect_translations_from_responses(_decoded_subtask_responses(snapshot))


def _collect_source_texts_from_cache(snapshot) -> dict[str, str]:
    source_texts: dict[str, str] = {}
    for subtask in snapshot.subtasks:
        req = subtask.request_payload or {}
        segments = req.get("segments", [])
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_id = segment.get("segment_id")
            if not isinstance(segment_id, str) or segment_id in source_texts:
                continue
            source_texts[segment_id] = str(
                segment.get("original_text") or segment.get("prompt_text") or ""
            )
    return source_texts


def _source_segments_fingerprint(segments: list[tuple[str, str]]) -> str:
    if not segments:
        return ""
    digest = hashlib.sha256()
    for segment_id, source_text in sorted(
        segments, key=lambda item: _segment_sort_key(item[0])
    ):
        digest.update(segment_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _term_audit_fingerprint(
    items: list[dict[str, object]], glossary: Glossary
) -> str:
    if not items or not glossary.entries:
        return ""
    digest = hashlib.sha256()
    digest.update(_TERM_AUDIT_VERSION.encode("ascii"))
    digest.update(b"\0")
    for entry in glossary.entries:
        digest.update(entry.src.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.dst.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.info.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry.regex).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.case_sensitive).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.enabled).encode("ascii"))
        digest.update(b"\0")
    digest.update(b"\1")
    for item in items:
        digest.update(str(item.get("segment_id", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.get("src", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.get("dst", "")).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:24]


def _normalized_similarity_text(text: str) -> str:
    return _TEXT_SIMILARITY_NORMALIZE_RE.sub("", text)


def _similarity_from_normalized(left_norm: str, right_norm: str) -> float:
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def _similarity_and_overlap_from_normalized(
    left_norm: str, right_norm: str
) -> tuple[float, float]:
    if not left_norm or not right_norm:
        return (0.0, 0.0)
    if left_norm == right_norm:
        return (1.0, 1.0)
    matcher = SequenceMatcher(None, left_norm, right_norm, autojunk=False)
    blocks = matcher.get_matching_blocks()
    matches = sum(block.size for block in blocks)
    similarity = matcher.ratio()
    shortest = min(len(left_norm), len(right_norm))
    overlap = matches / shortest if shortest > 0 else 0.0
    return (similarity, overlap)


def _similarity(left: str, right: str) -> float:
    left_norm = _normalized_similarity_text(left)
    right_norm = _normalized_similarity_text(right)
    return _similarity_from_normalized(left_norm, right_norm)


def _overlap_ratio_from_normalized(left_norm: str, right_norm: str) -> float:
    shortest = min(len(left_norm), len(right_norm))
    if shortest == 0:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    matcher = SequenceMatcher(None, left_norm, right_norm, autojunk=False)
    overlap = sum(block.size for block in matcher.get_matching_blocks())
    return overlap / shortest


def _overlap_ratio(left: str, right: str) -> float:
    left_norm = _normalized_similarity_text(left)
    right_norm = _normalized_similarity_text(right)
    return _overlap_ratio_from_normalized(left_norm, right_norm)


def _normalized_length(text: str) -> int:
    return len(_normalized_similarity_text(text))


def _looks_like_duplicate_translation(
    left_src: str,
    right_src: str,
    left_dst: str,
    right_dst: str,
) -> bool:
    shortest_translation = min(
        _normalized_length(left_dst),
        _normalized_length(right_dst),
    )
    if shortest_translation < _DUPLICATE_SCAN_MIN_TRANSLATION_LENGTH:
        return False
    source_overlap = max(
        _similarity(left_src, right_src),
        _overlap_ratio(left_src, right_src),
    )
    translation_similarity = _similarity(left_dst, right_dst)
    translation_overlap = _overlap_ratio(left_dst, right_dst)
    if shortest_translation < _DUPLICATE_SCAN_SHORT_TEXT_LENGTH:
        return (
            source_overlap < _DUPLICATE_SCAN_SHORT_SOURCE_RATIO
            and translation_similarity >= _DUPLICATE_SCAN_SHORT_TRANSLATION_RATIO
        )
    if source_overlap >= _DUPLICATE_SCAN_SOURCE_RATIO:
        return False
    if translation_similarity >= _DUPLICATE_SCAN_TRANSLATION_RATIO:
        return True
    return (
        translation_overlap >= _DUPLICATE_SCAN_OVERLAP_RATIO
        and translation_overlap - source_overlap >= _DUPLICATE_SCAN_MIN_DELTA
    )


def _looks_like_tagged_duplicate_partner(
    left_src: str,
    right_src: str,
    left_dst: str,
    right_dst: str,
) -> bool:
    shortest_translation = min(
        _normalized_length(left_dst),
        _normalized_length(right_dst),
    )
    if shortest_translation < _DUPLICATE_SCAN_MIN_TRANSLATION_LENGTH:
        return False
    source_overlap = max(
        _similarity(left_src, right_src),
        _overlap_ratio(left_src, right_src),
    )
    if source_overlap >= _DUPLICATE_SCAN_SOURCE_RATIO:
        return False
    return (
        _similarity(left_dst, right_dst) >= _DUPLICATE_SCAN_TAGGED_PARTNER_RATIO
        or _overlap_ratio(left_dst, right_dst) >= _DUPLICATE_SCAN_TAGGED_PARTNER_OVERLAP
    )


def _mark_item_risk(item: dict[str, object], tag: str, reason: str) -> None:
    tags = item.setdefault("tags", [])
    if isinstance(tags, list) and tag not in tags:
        tags.append(tag)
    reasons = item.setdefault("reasons", [])
    if isinstance(reasons, list) and reason not in reasons:
        reasons.append(reason)


def _mark_possible_duplicate(item: dict[str, object]) -> None:
    _mark_item_risk(item, _POSSIBLE_DUPLICATE_TAG, _POSSIBLE_DUPLICATE_REASON)


def _append_unique_values(item: dict[str, object], key: str, values: object) -> None:
    if not isinstance(values, list):
        return
    target = item.setdefault(key, [])
    if not isinstance(target, list):
        return
    for value in values:
        if isinstance(value, str) and value not in target:
            target.append(value)


def _glossary_from_metadata(value: object) -> Glossary:
    if not isinstance(value, list):
        return Glossary.empty()
    records = [item for item in value if isinstance(item, Mapping)]
    return Glossary.from_records(records)


def _append_glossary_term_audit(
    item: dict[str, object],
    entry: GlossaryEntry,
    *,
    applied: bool,
) -> dict[str, object] | None:
    terms = item.setdefault("glossary_terms", [])
    if not isinstance(terms, list):
        return None
    record: dict[str, object] = {
        "src": entry.src,
        "dst": entry.dst,
        "info": entry.info,
        "applied": applied,
        "inconsistent": False,
    }
    terms.append(record)
    return record


def _tag_term_glossary_risks(
    items: list[dict[str, object]], glossary: Glossary
) -> None:
    if not items or not glossary.entries:
        return

    for item in items:
        source_text = str(item.get("src", ""))
        translated_text = str(item.get("dst", ""))
        matched = glossary.match(source_text)
        if not matched:
            continue
        seen_keys: set[tuple[str, str, str, bool, bool]] = set()
        for entry in matched:
            key = _glossary_entry_key(entry)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            applied = target_term_present(entry, translated_text)
            audit_record = _append_glossary_term_audit(
                item,
                entry,
                applied=applied,
            )
            if audit_record is None:
                continue
            if not applied:
                _mark_item_risk(
                    item,
                    _GLOSSARY_NOT_APPLIED_TAG,
                    _GLOSSARY_NOT_APPLIED_REASON,
                )


def _term_audit_annotations(
    items: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    annotations: dict[str, dict[str, object]] = {}
    term_tags = {_GLOSSARY_NOT_APPLIED_TAG, _TERM_INCONSISTENCY_TAG}
    term_reasons = {_GLOSSARY_NOT_APPLIED_REASON, _TERM_INCONSISTENCY_REASON}
    for item in items:
        segment_id = item.get("segment_id")
        if not isinstance(segment_id, str):
            continue
        raw_tags = item.get("tags", [])
        raw_reasons = item.get("reasons", [])
        raw_terms = item.get("glossary_terms", [])
        tags: list[str] = []
        if isinstance(raw_tags, list):
            tags = [
                tag
                for tag in raw_tags
                if isinstance(tag, str) and tag in term_tags
            ]
        reasons: list[str] = []
        if isinstance(raw_reasons, list):
            reasons = [
                reason
                for reason in raw_reasons
                if isinstance(reason, str) and reason in term_reasons
            ]
        terms: list[dict[str, object]] = []
        if isinstance(raw_terms, list):
            terms = [
                dict(term)
                for term in raw_terms
                if isinstance(term, Mapping)
            ]
        if tags or reasons or terms:
            annotations[segment_id] = {
                "tags": tags,
                "reasons": reasons,
                "glossary_terms": terms,
            }
    return annotations


def _apply_term_audit_annotations(
    items: list[dict[str, object]], annotations: Mapping[str, Mapping[str, object]]
) -> None:
    for item in items:
        segment_id = item.get("segment_id")
        if not isinstance(segment_id, str):
            continue
        annotation = annotations.get(segment_id)
        if not annotation:
            continue
        _append_unique_values(item, "tags", annotation.get("tags"))
        _append_unique_values(item, "reasons", annotation.get("reasons"))
        terms = annotation.get("glossary_terms")
        if isinstance(terms, list):
            item["glossary_terms"] = [
                dict(term) for term in terms if isinstance(term, Mapping)
            ]


def _tag_possible_adjacent_duplicates(items: list[dict[str, object]]) -> None:
    sources = [str(item.get("src", "")) for item in items]
    destinations = [str(item.get("dst", "")).strip() for item in items]
    source_norms = [_normalized_similarity_text(source) for source in sources]
    destination_norms = [
        _normalized_similarity_text(destination) for destination in destinations
    ]
    destination_lengths = [len(destination) for destination in destination_norms]
    translation_metric_cache: dict[tuple[int, int], tuple[float, float, int]] = {}
    source_overlap_cache: dict[tuple[int, int], float] = {}

    def pair_key(left_index: int, right_index: int) -> tuple[int, int]:
        return (
            (left_index, right_index)
            if left_index < right_index
            else (right_index, left_index)
        )

    def translation_metrics(
        left_index: int, right_index: int
    ) -> tuple[float, float, int]:
        key = pair_key(left_index, right_index)
        cached = translation_metric_cache.get(key)
        if cached is not None:
            return cached
        translation_similarity, translation_overlap = (
            _similarity_and_overlap_from_normalized(
                destination_norms[left_index],
                destination_norms[right_index],
            )
        )
        shortest_translation = min(
            destination_lengths[left_index],
            destination_lengths[right_index],
        )
        cached = (translation_similarity, translation_overlap, shortest_translation)
        translation_metric_cache[key] = cached
        return cached

    def source_overlap(left_index: int, right_index: int) -> float:
        key = pair_key(left_index, right_index)
        cached = source_overlap_cache.get(key)
        if cached is not None:
            return cached
        source_similarity, source_common_overlap = (
            _similarity_and_overlap_from_normalized(
                source_norms[left_index],
                source_norms[right_index],
            )
        )
        cached = max(source_similarity, source_common_overlap)
        source_overlap_cache[key] = cached
        return cached

    def duplicate_match(left_index: int, right_index: int) -> bool:
        (
            translation_similarity,
            translation_overlap,
            shortest_translation,
        ) = translation_metrics(left_index, right_index)
        if shortest_translation < _DUPLICATE_SCAN_MIN_TRANSLATION_LENGTH:
            return False
        if shortest_translation < _DUPLICATE_SCAN_SHORT_TEXT_LENGTH:
            return (
                translation_similarity >= _DUPLICATE_SCAN_SHORT_TRANSLATION_RATIO
                and source_overlap(left_index, right_index)
                < _DUPLICATE_SCAN_SHORT_SOURCE_RATIO
            )
        if (
            translation_similarity < _DUPLICATE_SCAN_TRANSLATION_RATIO
            and translation_overlap < _DUPLICATE_SCAN_OVERLAP_RATIO
        ):
            return False
        overlap = source_overlap(left_index, right_index)
        if overlap >= _DUPLICATE_SCAN_SOURCE_RATIO:
            return False
        if translation_similarity >= _DUPLICATE_SCAN_TRANSLATION_RATIO:
            return True
        return translation_overlap - overlap >= _DUPLICATE_SCAN_MIN_DELTA

    def tagged_partner_match(left_index: int, right_index: int) -> bool:
        (
            translation_similarity,
            translation_overlap,
            shortest_translation,
        ) = translation_metrics(left_index, right_index)
        if shortest_translation < _DUPLICATE_SCAN_MIN_TRANSLATION_LENGTH:
            return False
        if (
            translation_similarity < _DUPLICATE_SCAN_TAGGED_PARTNER_RATIO
            and translation_overlap < _DUPLICATE_SCAN_TAGGED_PARTNER_OVERLAP
        ):
            return False
        return source_overlap(left_index, right_index) < _DUPLICATE_SCAN_SOURCE_RATIO

    tagged_indices = {
        index
        for index, item in enumerate(items)
        if _POSSIBLE_DUPLICATE_TAG in item.get("tags", [])
    }
    for index in tagged_indices:
        window_start = max(0, index - _DUPLICATE_SCAN_WINDOW)
        window_end = min(len(items), index + _DUPLICATE_SCAN_WINDOW + 1)
        for right_index in range(window_start, window_end):
            if right_index == index:
                continue
            if (
                min(destination_lengths[index], destination_lengths[right_index])
                < _DUPLICATE_SCAN_MIN_TRANSLATION_LENGTH
            ):
                continue
            if tagged_partner_match(index, right_index):
                _mark_possible_duplicate(items[right_index])

    for left_index, left in enumerate(items):
        right_end = min(len(items), left_index + _DUPLICATE_SCAN_WINDOW + 1)
        for right_index in range(left_index + 1, right_end):
            if (
                min(destination_lengths[left_index], destination_lengths[right_index])
                < _DUPLICATE_SCAN_MIN_TRANSLATION_LENGTH
            ):
                continue
            if duplicate_match(left_index, right_index):
                _mark_possible_duplicate(left)
                _mark_possible_duplicate(items[right_index])


def _coerce_cached_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n", ""}:
            return False
    if value is None:
        return default
    return bool(value)


def _text_preserve_rules_from_metadata(value: object):
    from transoria.workflows.translation.rules import TextPreserveRule  # noqa: PLC0415

    if not isinstance(value, list):
        return ()
    rules: list[TextPreserveRule] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        pattern = str(item.get("pattern", "")).strip()
        if not pattern:
            continue
        rules.append(
            TextPreserveRule(
                pattern=pattern,
                note=str(item.get("note", "")),
                enabled=_coerce_cached_bool(item.get("enabled", True), default=True),
            )
        )
    return tuple(rules)


def _replacement_rules_from_metadata(value: object):
    from transoria.workflows.translation.rules import ReplacementRule  # noqa: PLC0415

    if not isinstance(value, list):
        return ()
    rules: list[ReplacementRule] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        src = str(item.get("src", "")).strip()
        if not src:
            continue
        rules.append(
            ReplacementRule(
                src=src,
                dst=str(item.get("dst", "")),
                regex=_coerce_cached_bool(item.get("regex", False)),
                case_sensitive=_coerce_cached_bool(item.get("case_sensitive", False)),
                note=str(item.get("note", "")),
                enabled=_coerce_cached_bool(item.get("enabled", True), default=True),
            )
        )
    return tuple(rules)


def _build_handlers(service: TaskService) -> dict[str, object]:
    term_audit_cache: OrderedDict[
        tuple[str, str], dict[str, dict[str, object]]
    ] = OrderedDict()

    def require_proofreadable_translation_task(task_id: str):
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
        if snapshot.record.status not in _PROOFREADABLE_TRANSLATION_STATUSES:
            raise BridgeError.conflict(
                "proofreading is only available after translation stops or completes.",
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
            if snapshot.record.status not in _PROOFREADABLE_TRANSLATION_STATUSES:
                continue
            if not snapshot.subtasks:
                continue
            out.append(header)
        return {"tasks": out}

    def load_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
        task_id = expect_string(payload, "task_id")
        snapshot = require_proofreadable_translation_task(task_id)

        # Aggregate per-segment data across subtasks. Latest write wins
        # (split children override the parent) so edits via update_segment
        # always read back consistently.
        decoded_responses = _decoded_subtask_responses(snapshot)
        translations, response_low_confidence = _collect_segment_state_from_responses(
            decoded_responses
        )
        low_conf_ids: set[str] = set(response_low_confidence)
        seg_tags: dict[str, list[str]] = {
            sid: list(record["tags"])
            for sid, record in response_low_confidence.items()
            if record["tags"]
        }
        seg_reasons: dict[str, list[str]] = {
            sid: list(record["reasons"])
            for sid, record in response_low_confidence.items()
            if record["reasons"]
        }

        metadata = snapshot.record.metadata
        glossary = _glossary_from_metadata(metadata.get("glossary"))
        try:
            source_language = Language(str(metadata.get("source_language", "")))
        except ValueError:
            source_language = None
        try:
            target_language = Language(str(metadata.get("target_language", "")))
        except ValueError:
            target_language = None

        # Build (segment_id, src) map. Each segment appears exactly once
        # in the parent subtask's request_payload, but split children
        # repeat it. Use the first occurrence for source text — they all
        # share the same `original_text` slot anyway.
        seen: dict[str, dict[str, object]] = {}
        seg_subtasks: dict[str, list[str]] = {}
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
                owners = seg_subtasks.setdefault(seg_id, [])
                if subtask.id not in owners:
                    owners.append(subtask.id)
                if seg_id in seen:
                    continue
                src = str(segment.get("original_text", ""))
                dst = translations.get(seg_id)
                has_cached_translation = dst is not None
                if dst is None:
                    dst = src
                    low_conf_ids.add(seg_id)
                    reasons = seg_reasons.setdefault(seg_id, [])
                    if "missing_translation_fell_back_to_source" not in reasons:
                        reasons.append("missing_translation_fell_back_to_source")
                    tags = seg_tags.setdefault(seg_id, [])
                    if "source_residue" not in tags:
                        tags.append("source_residue")
                if has_cached_translation:
                    verdict = evaluate_segment_confidence(
                        src,
                        dst,
                        min_length_ratio=0.25,
                        max_length_ratio=4.0,
                        max_punctuation_delta=12,
                        source_language=source_language,
                        target_language=target_language,
                    )
                    if verdict.is_low_confidence:
                        low_conf_ids.add(seg_id)
                        reasons = seg_reasons.setdefault(seg_id, [])
                        for reason in verdict.reasons:
                            if reason not in reasons:
                                reasons.append(reason)
                        tags = seg_tags.setdefault(seg_id, [])
                        for tag in verdict.tags:
                            if tag not in tags:
                                tags.append(tag)
                item: dict[str, object] = {
                    "segment_id": seg_id,
                    "src": src,
                    "dst": dst,
                    "low_confidence": seg_id in low_conf_ids,
                    "subtask_ids": owners,
                }
                tags_for_seg = seg_tags.get(seg_id)
                if tags_for_seg:
                    item["tags"] = tags_for_seg
                reasons_for_seg = seg_reasons.get(seg_id)
                if reasons_for_seg:
                    item["reasons"] = reasons_for_seg
                seen[seg_id] = item

        items = sorted(
            seen.values(), key=lambda i: _segment_sort_key(str(i["segment_id"]))
        )
        audit_fingerprint = _term_audit_fingerprint(items, glossary)
        if audit_fingerprint:
            audit_key = (task_id, audit_fingerprint)
            cached_audit = term_audit_cache.get(audit_key)
            if cached_audit is None:
                _tag_term_glossary_risks(items, glossary)
                cached_audit = _term_audit_annotations(items)
                term_audit_cache[audit_key] = cached_audit
                term_audit_cache.move_to_end(audit_key)
                while len(term_audit_cache) > _TERM_AUDIT_CACHE_MAX_TASKS:
                    term_audit_cache.popitem(last=False)
            else:
                term_audit_cache.move_to_end(audit_key)
                _apply_term_audit_annotations(items, cached_audit)
        _tag_possible_adjacent_duplicates(items)
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
        snapshot = require_proofreadable_translation_task(task_id)

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
                if (
                    isinstance(segment, dict)
                    and segment.get("segment_id") == segment_id
                ):
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
            mark_accepted_override(current, segment_id)
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
            BILINGUAL_OUTPUT_FOLDER_EN,
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
        snapshot = require_proofreadable_translation_task(task_id)

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
        # Regen does not call the LLM, but `_prepare_segments` must replay
        # cached preserve/pre-replacement rules because rewritten prompt text
        # can change the second language-filter pass and therefore the
        # segment_id list. Post-replacements are not replayed: cached
        # translations are already past that stage.
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
            text_preserve_rules=_text_preserve_rules_from_metadata(
                record_metadata.get("text_preserve_rules", [])
            ),
            pre_replacements=_replacement_rules_from_metadata(
                record_metadata.get("pre_replacements", [])
            ),
            bilingual_enabled=export_bilingual,
            bilingual_subfolder=str(
                record_metadata.get("bilingual_subfolder") or ""
            ) or BILINGUAL_OUTPUT_FOLDER_EN,
            bilingual_dedup_when_same=_coerce_cached_bool(
                record_metadata.get("bilingual_dedup_when_same", True),
                default=True,
            ),
        )

        try:
            parsed_files = _scan_and_parse(config.input_dir, buffer_epub_archives=False)
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
        cached_source_texts = _collect_source_texts_from_cache(snapshot)
        if snapshot.record.status is TaskStatus.COMPLETED:
            mismatched_files = []
            for parsed in parsed_files:
                prepared_segments = prepared_per_file.get(parsed.file_index, [])
                expected_segments = len(prepared_segments)
                matched_segments = sum(
                    1
                    for segment in prepared_segments
                    if segment.segment_id in translations_by_segment
                )
                if 0 < matched_segments < expected_segments:
                    current_source_segments = [
                        (segment.segment_id, segment.original_text)
                        for segment in prepared_segments
                    ]
                    cached_source_segments = [
                        (segment_id, source_text)
                        for segment_id, source_text in cached_source_texts.items()
                        if segment_id.split(":", 1)[0] == str(parsed.file_index)
                    ]
                    first_missing_segment_id = next(
                        (
                            segment.segment_id
                            for segment in prepared_segments
                            if segment.segment_id not in translations_by_segment
                        ),
                        "",
                    )
                    mismatched_files.append(
                        {
                            "path": str(parsed.document.path),
                            "reason": (
                                "completed task cache no longer matches parsed source "
                                "segments"
                            ),
                            "code": "cache_segment_mismatch",
                            "details": {
                                "expected_segments": expected_segments,
                                "matched_segments": matched_segments,
                                "missing_segments": expected_segments
                                - matched_segments,
                                "parsed_source_fingerprint": _source_segments_fingerprint(
                                    current_source_segments
                                ),
                                "cache_source_fingerprint": _source_segments_fingerprint(
                                    cached_source_segments
                                ),
                                "first_missing_segment_id": first_missing_segment_id,
                            },
                        }
                    )
            if mismatched_files:
                return {
                    "task_id": task_id,
                    "translated_files": [],
                    "bilingual_files": [],
                    "failed_files": mismatched_files,
                }
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
        raw_segment_ids = payload.get("segment_ids")
        segment_ids = None
        if raw_segment_ids is not None:
            if not isinstance(raw_segment_ids, list) or not all(
                isinstance(item, str) and item.strip() for item in raw_segment_ids
            ):
                raise BridgeError.invalid_argument(
                    "segment_ids must be a list of non-empty strings.",
                    field="segment_ids",
                )
            segment_ids = [item.strip() for item in raw_segment_ids]
        return service.start_retranslate(
            task_id=expect_string(payload, "task_id"),
            segment_id=expect_string(payload, "segment_id"),
            segment_ids=segment_ids,
            model_id=_optional_string(payload, "model_id"),
            prompt_preset_id=_optional_string(payload, "prompt_preset_id"),
        )

    def retranslate_status(payload: Mapping[str, object]) -> dict[str, object]:
        return service.read_retranslate_status(
            request_id=expect_string(payload, "request_id"),
        )

    def resume_retranslate(payload: Mapping[str, object]) -> dict[str, object]:
        return service.resume_retranslate(
            request_id=expect_string(payload, "request_id"),
        )

    return {
        "proofreading.list_tasks": list_tasks,
        "proofreading.load_snapshot": load_snapshot,
        "proofreading.update_segment": update_segment,
        "proofreading.regenerate_outputs": regenerate_outputs,
        "proofreading.retranslate_segment": retranslate_segment,
        "proofreading.retranslate_status": retranslate_status,
        "proofreading.resume_retranslate": resume_retranslate,
    }


def register(router: BridgeRouter, *, service: TaskService) -> None:
    for method, handler in _build_handlers(service).items():
        router.register(method, handler)


__all__ = ["register"]
