from __future__ import annotations

import pytest

from transoria.domain import SubtaskStatus
from transoria.runtime import Subtask


def test_subtask_round_trip_via_dict() -> None:
    original = Subtask(
        id="s1",
        task_id="t1",
        status=SubtaskStatus.COMPLETED,
        request_payload={"lines": {"0": "hello"}},
        response_content='{"0":"안녕"}',
        input_tokens=12,
        output_tokens=8,
        attempt_count=2,
        last_error="",
    )

    restored = Subtask.from_dict(original.to_dict())

    assert restored == original
    assert restored.total_tokens == 20


def test_subtask_round_trip_via_json() -> None:
    original = Subtask(id="s2", task_id="t1", status=SubtaskStatus.PENDING)

    assert Subtask.from_json(original.to_json()) == original


def test_subtask_with_status_returns_new_instance() -> None:
    original = Subtask(id="s3", task_id="t1", status=SubtaskStatus.PENDING)

    updated = original.with_status(SubtaskStatus.RUNNING)

    assert original.status is SubtaskStatus.PENDING
    assert updated.status is SubtaskStatus.RUNNING
    assert updated is not original


def test_subtask_from_dict_rejects_invalid_status() -> None:
    with pytest.raises(ValueError, match="Invalid subtask status"):
        Subtask.from_dict({"id": "x", "task_id": "t", "status": "invalid"})


def test_subtask_from_dict_rejects_non_mapping_payload() -> None:
    with pytest.raises(ValueError, match="request_payload"):
        Subtask.from_dict(
            {"id": "x", "task_id": "t", "request_payload": ["not", "a", "mapping"]}
        )
