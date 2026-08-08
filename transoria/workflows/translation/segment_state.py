"""Helpers for reading latest translation state from cached subtask payloads."""

from __future__ import annotations

import json
from typing import Iterable, Mapping, Protocol

from transoria.domain import SubtaskStatus


class TranslationSubtaskLike(Protocol):
    status: SubtaskStatus
    response_content: str


LowConfidenceMap = dict[str, dict[str, list[str]]]
ACCEPTED_OVERRIDE_SEGMENTS_KEY = "accepted_overrides"
PRESERVED_CANDIDATE_SEGMENTS_KEY = "preserved_candidates"


def mark_accepted_override(payload: dict[str, object], segment_id: str) -> None:
    raw_segment_ids = payload.get(ACCEPTED_OVERRIDE_SEGMENTS_KEY)
    if isinstance(raw_segment_ids, list):
        segment_ids = [str(item) for item in raw_segment_ids if item not in (None, "")]
    else:
        segment_ids = []
    if segment_id not in segment_ids:
        segment_ids.append(segment_id)
    payload[ACCEPTED_OVERRIDE_SEGMENTS_KEY] = segment_ids


def accepted_override_payload(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    return _selected_translation_payload(
        payload,
        (ACCEPTED_OVERRIDE_SEGMENTS_KEY,),
    )


def authoritative_failed_payload(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    return _selected_translation_payload(
        payload,
        (
            ACCEPTED_OVERRIDE_SEGMENTS_KEY,
            PRESERVED_CANDIDATE_SEGMENTS_KEY,
        ),
    )


def _selected_translation_payload(
    payload: Mapping[str, object],
    keys: tuple[str, ...],
) -> dict[str, object] | None:
    segment_ids: list[str] = []
    for key in keys:
        raw_segment_ids = payload.get(key)
        if not isinstance(raw_segment_ids, list):
            continue
        for item in raw_segment_ids:
            if item in (None, ""):
                continue
            segment_id = str(item)
            if segment_id not in segment_ids:
                segment_ids.append(segment_id)
    if not segment_ids:
        return None

    raw_translations = payload.get("translations")
    if not isinstance(raw_translations, Mapping):
        return None
    translations = {
        segment_id: str(raw_translations[segment_id])
        for segment_id in segment_ids
        if segment_id in raw_translations
    }
    if not translations:
        return None

    raw_low_confidence = payload.get("low_confidence", [])
    low_confidence = []
    if isinstance(raw_low_confidence, list):
        accepted = set(translations)
        low_confidence = [
            entry
            for entry in raw_low_confidence
            if isinstance(entry, Mapping)
            and str(entry.get("segment_id", "")) in accepted
        ]
    return {
        "version": payload.get("version", 2),
        "translations": translations,
        "low_confidence": low_confidence,
    }


def low_confidence_by_segment(payload: Mapping[str, object]) -> LowConfidenceMap:
    entries_by_segment: LowConfidenceMap = {}
    raw_entries = payload.get("low_confidence", [])
    if not isinstance(raw_entries, list):
        return entries_by_segment
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            continue
        raw_segment_id = entry.get("segment_id")
        if raw_segment_id in (None, ""):
            continue
        segment_id = str(raw_segment_id)
        record = entries_by_segment.setdefault(segment_id, {"reasons": [], "tags": []})
        reasons = entry.get("reasons", [])
        if isinstance(reasons, list):
            for reason in reasons:
                if isinstance(reason, str) and reason not in record["reasons"]:
                    record["reasons"].append(reason)
        tags = entry.get("tags", [])
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag not in record["tags"]:
                    record["tags"].append(tag)
    return entries_by_segment


def collect_segment_state_from_payloads(
    payloads: Iterable[Mapping[str, object]],
) -> tuple[dict[str, str], LowConfidenceMap]:
    """Return latest translations and matching low-confidence metadata.

    Later payloads are authoritative per segment. If a later payload updates a
    segment without reporting low confidence, any stale low-confidence record
    from an earlier attempt is cleared.
    """

    translations: dict[str, str] = {}
    low_confidence: LowConfidenceMap = {}
    for payload in payloads:
        records = payload.get("translations")
        if isinstance(records, Mapping):
            payload_low_confidence = low_confidence_by_segment(payload)
            for segment_id, text in records.items():
                sid = str(segment_id)
                translations[sid] = str(text)
                if sid in payload_low_confidence:
                    low_confidence[sid] = payload_low_confidence[sid]
                else:
                    low_confidence.pop(sid, None)
            continue
        # Legacy flat responses are authoritative for their segment ids.
        for segment_id, text in payload.items():
            if not isinstance(segment_id, str) or ":" not in segment_id:
                continue
            translations[segment_id] = str(text)
            low_confidence.pop(segment_id, None)
    return translations, low_confidence


def collect_segment_state_from_completed_subtasks(
    subtasks: Iterable[TranslationSubtaskLike],
) -> tuple[dict[str, str], LowConfidenceMap]:
    payloads: list[Mapping[str, object]] = []
    for subtask in subtasks:
        if subtask.status is not SubtaskStatus.COMPLETED:
            continue
        if not subtask.response_content:
            continue
        try:
            payload = json.loads(subtask.response_content)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, Mapping):
            payloads.append(payload)
    return collect_segment_state_from_payloads(payloads)


def collect_segment_state_from_authoritative_subtasks(
    subtasks: Iterable[TranslationSubtaskLike],
) -> tuple[dict[str, str], LowConfidenceMap]:
    """Collect authoritative output while ignoring diagnostic failed payloads.

    Completed subtasks are workflow-authoritative. Non-completed subtasks can
    still contain user-accepted edits, retranslation results, or useful mixed
    target-language candidates. Only explicitly marked segments participate in
    final output; unmarked diagnostic text remains excluded.
    """

    payloads: list[Mapping[str, object]] = []
    for subtask in subtasks:
        if not subtask.response_content:
            continue
        try:
            payload = json.loads(subtask.response_content)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        if subtask.status is SubtaskStatus.COMPLETED:
            payloads.append(payload)
            continue
        failed_payload = authoritative_failed_payload(payload)
        if failed_payload is not None:
            payloads.append(failed_payload)
    return collect_segment_state_from_payloads(payloads)
