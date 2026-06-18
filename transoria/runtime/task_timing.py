"""Helpers for persisted task runtime accounting."""

from __future__ import annotations

from datetime import datetime, timezone

from transoria.domain import TaskStatus
from transoria.runtime.task_record import TaskRecord


RUNTIME_ELAPSED_SECONDS_KEY = "runtime_elapsed_seconds"
RUNTIME_STARTED_AT_KEY = "runtime_started_at"
_ACTIVE_STATUSES = frozenset(
    {TaskStatus.RUNNING, TaskStatus.STOPPING, TaskStatus.PAUSING}
)


def runtime_metadata_for_status(
    record: TaskRecord, next_status: TaskStatus, now_iso: str
) -> dict[str, object]:
    metadata = dict(record.metadata)
    previous_active = record.status in _ACTIVE_STATUSES
    next_active = next_status in _ACTIVE_STATUSES
    accumulated = _coerce_nonnegative_float(
        metadata.get(RUNTIME_ELAPSED_SECONDS_KEY), 0.0
    )

    if next_active and not previous_active:
        metadata[RUNTIME_ELAPSED_SECONDS_KEY] = accumulated
        metadata[RUNTIME_STARTED_AT_KEY] = now_iso
        return metadata

    if previous_active and next_active:
        if _parse_iso_timestamp(metadata.get(RUNTIME_STARTED_AT_KEY)) is None:
            metadata[RUNTIME_STARTED_AT_KEY] = _fallback_started_at(record) or now_iso
        metadata[RUNTIME_ELAPSED_SECONDS_KEY] = accumulated
        return metadata

    if previous_active and not next_active:
        started = _parse_iso_timestamp(
            metadata.get(RUNTIME_STARTED_AT_KEY)
        ) or _parse_iso_timestamp(_fallback_started_at(record))
        now = _parse_iso_timestamp(now_iso)
        if started is not None and now is not None:
            accumulated += max(0.0, (now - started).total_seconds())
        metadata[RUNTIME_ELAPSED_SECONDS_KEY] = accumulated
        metadata.pop(RUNTIME_STARTED_AT_KEY, None)
        return metadata

    if RUNTIME_ELAPSED_SECONDS_KEY in metadata:
        metadata[RUNTIME_ELAPSED_SECONDS_KEY] = accumulated
    metadata.pop(RUNTIME_STARTED_AT_KEY, None)
    return metadata


def _fallback_started_at(record: TaskRecord) -> str:
    if record.updated_at:
        return record.updated_at
    return record.created_at


def elapsed_seconds_for_record(record: TaskRecord) -> float:
    metadata = record.metadata
    if (
        RUNTIME_ELAPSED_SECONDS_KEY in metadata
        or RUNTIME_STARTED_AT_KEY in metadata
    ):
        elapsed = _coerce_nonnegative_float(
            metadata.get(RUNTIME_ELAPSED_SECONDS_KEY), 0.0
        )
        started = _parse_iso_timestamp(metadata.get(RUNTIME_STARTED_AT_KEY))
        if record.status in _ACTIVE_STATUSES and started is not None:
            elapsed += max(
                0.0,
                (datetime.now(timezone.utc) - started).total_seconds(),
            )
        return max(0.0, elapsed)

    start = _parse_iso_timestamp(record.created_at)
    if start is None:
        return 0.0
    if record.status is TaskStatus.RUNNING:
        end = datetime.now(timezone.utc)
    else:
        end = _parse_iso_timestamp(record.updated_at) or datetime.now(timezone.utc)
    return max(0.0, (end - start).total_seconds())


def _parse_iso_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, parsed)
