from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.runtime import (
    Subtask,
    SubtaskFailedWithResult,
    SubtaskResult,
    TaskCache,
    TaskRecord,
)
from transoria.workflows.translation.orchestrator import (
    _prepare_segment_recovery,
    _segment_recovery_candidates,
)
from transoria.workflows.translation.runner import (
    RECOVERY_SEGMENT_IDS_KEY,
    TranslationRecoveryRunner,
    split_segment_payload_batches,
)


def _segment(index: int, text: str = "짧은 문장") -> dict[str, object]:
    return {
        "segment_id": f"0:{index}",
        "chunk_index": index,
        "prompt_text": text,
        "original_text": text,
        "protection_spans": [],
        "leading_whitespace": "",
        "trailing_whitespace": "",
    }


def test_split_segment_payload_batches_uses_five_row_batches() -> None:
    batches = split_segment_payload_batches([_segment(index) for index in range(12)])

    assert [len(batch) for batch in batches] == [5, 5, 2]


def test_split_segment_payload_batches_keeps_long_rows_small() -> None:
    batches = split_segment_payload_batches(
        [_segment(0, "가" * 3000), _segment(1), _segment(2)],
        token_cap=10,
    )

    assert [len(batch) for batch in batches] == [1, 2]


@dataclass
class CapturingRunner:
    calls: list[Subtask] = field(default_factory=list)
    fail_batch: int | None = None

    async def run(self, subtask: Subtask) -> SubtaskResult:
        self.calls.append(subtask)
        batch_index = len(self.calls)
        translations = {
            str(segment["segment_id"]): f"译文-{segment['segment_id']}"
            for segment in subtask.request_payload["segments"]
        }
        result = SubtaskResult(
            response_content=json.dumps(
                {"version": 2, "translations": translations, "low_confidence": []},
                ensure_ascii=False,
            ),
            input_tokens=10,
            output_tokens=5,
        )
        if self.fail_batch == batch_index:
            raise SubtaskFailedWithResult("batch failed", result=result)
        return result


def test_recovery_runner_batches_and_merges_only_requested_segments() -> None:
    inner = CapturingRunner()
    runner = TranslationRecoveryRunner(inner)  # type: ignore[arg-type]
    segments = [_segment(index) for index in range(7)]
    subtask = Subtask(
        id="chunk-00001",
        task_id="translation-test",
        request_payload={
            "version": 1,
            "segments": segments,
            "context_lines": ["旧上下文"],
            RECOVERY_SEGMENT_IDS_KEY: [f"0:{index}" for index in range(1, 7)],
        },
        response_content=json.dumps(
            {
                "version": 2,
                "translations": {"0:0": "保留的正常译文", "0:1": "원문"},
                "low_confidence": [
                    {
                        "segment_id": "0:1",
                        "reasons": ["fell_back_to_source_after_max_retries"],
                        "tags": ["source_residue"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        input_tokens=100,
        output_tokens=50,
        cached_input_tokens=20,
    )

    result = asyncio.run(runner.run(subtask))
    payload = json.loads(result.response_content)

    assert [len(call.request_payload["segments"]) for call in inner.calls] == [5, 1]
    assert all(call.request_payload["context_lines"] == [] for call in inner.calls)
    assert payload["translations"]["0:0"] == "保留的正常译文"
    assert payload["translations"]["0:1"] == "译文-0:1"
    assert payload["low_confidence"] == []
    assert set(payload["accepted_overrides"]) == {f"0:{index}" for index in range(1, 7)}
    assert result.input_tokens == 120
    assert result.output_tokens == 60
    assert result.cached_input_tokens == 20


def test_recovery_runner_preserves_successful_batches_when_one_fails() -> None:
    inner = CapturingRunner(fail_batch=2)
    runner = TranslationRecoveryRunner(inner)  # type: ignore[arg-type]
    segments = [_segment(index) for index in range(6)]
    subtask = Subtask(
        id="chunk-00001",
        task_id="translation-test",
        request_payload={
            "version": 1,
            "segments": segments,
            RECOVERY_SEGMENT_IDS_KEY: [f"0:{index}" for index in range(6)],
        },
    )

    with pytest.raises(SubtaskFailedWithResult) as caught:
        asyncio.run(runner.run(subtask))

    payload = json.loads(caught.value.result.response_content)
    assert set(payload["translations"]) == {f"0:{index}" for index in range(6)}
    assert set(payload["accepted_overrides"]) == {f"0:{index}" for index in range(6)}


def test_prepare_segment_recovery_resets_only_owner_with_source_fallback(
    tmp_path,
) -> None:
    cache = TaskCache(tmp_path)
    task_id = "translation-recovery"
    clean = Subtask(
        id="chunk-00000",
        task_id=task_id,
        status=SubtaskStatus.COMPLETED,
        request_payload={"segments": [_segment(0)]},
        response_content=json.dumps(
            {"version": 2, "translations": {"0:0": "正常译文"}, "low_confidence": []},
            ensure_ascii=False,
        ),
    )
    fallback = Subtask(
        id="chunk-00001",
        task_id=task_id,
        status=SubtaskStatus.COMPLETED,
        request_payload={"segments": [_segment(1), _segment(2)]},
        response_content=json.dumps(
            {
                "version": 2,
                "translations": {"0:1": "원문", "0:2": "正常译文2"},
                "low_confidence": [
                    {
                        "segment_id": "0:1",
                        "reasons": ["fell_back_to_source_after_max_retries"],
                        "tags": ["source_residue"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    cache.write_seed(
        TaskRecord(
            id=task_id,
            kind=TaskKind.TRANSLATION,
            status=TaskStatus.FAILED,
        ),
        [clean, fallback],
    )

    snapshot = cache.load(task_id)
    assert _segment_recovery_candidates(snapshot.subtasks) == {"0:1"}
    _prepare_segment_recovery(cache, task_id, snapshot.subtasks)
    updated = {subtask.id: subtask for subtask in cache.load_subtasks(task_id)}

    assert updated["chunk-00000"].status is SubtaskStatus.COMPLETED
    assert updated["chunk-00001"].status is SubtaskStatus.PENDING
    assert updated["chunk-00001"].request_payload[RECOVERY_SEGMENT_IDS_KEY] == ["0:1"]


def test_prepare_segment_recovery_retries_http_400_as_whole_chunk(tmp_path) -> None:
    cache = TaskCache(tmp_path)
    task_id = "translation-http-400-recovery"
    failed = Subtask(
        id="chunk-00000",
        task_id=task_id,
        status=SubtaskStatus.FAILED,
        request_payload={"segments": [_segment(0), _segment(1)]},
        last_error="[llm.http_error] LlmRequestError: HTTP 400 from provider",
    )
    cache.write_seed(
        TaskRecord(id=task_id, kind=TaskKind.TRANSLATION, status=TaskStatus.FAILED),
        [failed],
    )

    snapshot = cache.load(task_id)
    assert _segment_recovery_candidates(snapshot.subtasks) == set()
    _prepare_segment_recovery(cache, task_id, snapshot.subtasks)
    updated = cache.load_subtasks(task_id)[0]

    assert updated.status is SubtaskStatus.PENDING
    assert RECOVERY_SEGMENT_IDS_KEY not in updated.request_payload
    assert [
        segment["segment_id"] for segment in updated.request_payload["segments"]
    ] == ["0:0", "0:1"]


def test_prepare_segment_recovery_targets_only_explicit_source_residue(
    tmp_path,
) -> None:
    cache = TaskCache(tmp_path)
    task_id = "translation-source-residue-recovery"
    failed = Subtask(
        id="chunk-00000",
        task_id=task_id,
        status=SubtaskStatus.FAILED,
        request_payload={"segments": [_segment(0), _segment(1)]},
        response_content=json.dumps(
            {
                "version": 2,
                "translations": {"0:0": "원문", "0:1": "正常译文"},
                "low_confidence": [
                    {
                        "segment_id": "0:0",
                        "reasons": ["fell_back_to_source_after_max_retries"],
                        "tags": ["source_residue"],
                    },
                    {
                        "segment_id": "0:1",
                        "reasons": ["possible duplicate"],
                        "tags": ["possible_duplicate"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        last_error="[translation.segment_recovery_failed] unresolved output",
    )
    cache.write_seed(
        TaskRecord(id=task_id, kind=TaskKind.TRANSLATION, status=TaskStatus.FAILED),
        [failed],
    )

    snapshot = cache.load(task_id)
    assert _segment_recovery_candidates(snapshot.subtasks) == {"0:0"}
    _prepare_segment_recovery(cache, task_id, snapshot.subtasks)
    updated = cache.load_subtasks(task_id)[0]

    assert updated.status is SubtaskStatus.PENDING
    assert updated.request_payload[RECOVERY_SEGMENT_IDS_KEY] == ["0:0"]


def test_segment_recovery_does_not_override_user_accepted_source_text() -> None:
    subtask = Subtask(
        id="chunk-00000",
        task_id="translation-test",
        status=SubtaskStatus.COMPLETED,
        request_payload={"segments": [_segment(0)]},
        response_content=json.dumps(
            {
                "version": 2,
                "translations": {"0:0": "원문"},
                "accepted_overrides": ["0:0"],
                "low_confidence": [
                    {
                        "segment_id": "0:0",
                        "reasons": ["Korean residue remains in translation"],
                        "tags": ["source_residue"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )

    assert _segment_recovery_candidates((subtask,)) == set()
