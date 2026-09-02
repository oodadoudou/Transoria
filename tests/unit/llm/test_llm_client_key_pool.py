"""LlmClient + KeyPool integration: round-robin, eviction, all-failed."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from transoria.domain import TaskKind
from transoria.llm.client import (
    ChatRequest,
    LlmClient,
    LlmDegenerateOutputError,
    LlmRequestError,
    TransportResult,
)
from transoria.llm.config import ModelConfig, ProviderFormat
from transoria.runtime.cache import TaskCache
from transoria.runtime.key_pool import KeyPool
from transoria.runtime.request_log import append_local_failure, request_log_scope
from transoria.runtime.task_record import TaskRecord


@dataclass
class RecordingTransport:
    queue: list[TransportResult]
    headers_seen: list[Mapping[str, str]] = field(default_factory=list)
    payloads_seen: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.headers_seen.append(dict(headers))
        self.payloads_seen.append(dict(payload))
        return self.queue.pop(0)


def _model(*keys: str) -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api",
        model_id="x",
        api_keys=tuple(keys),
    )


def _ok(content: str = "hi") -> TransportResult:
    return TransportResult(
        status_code=200,
        body={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


def _http(status: int, body: dict | None = None) -> TransportResult:
    return TransportResult(status_code=status, body=body or {"error": "x"})


def test_request_log_records_status_usage_and_response(tmp_path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(TaskRecord(id="task-1", kind=TaskKind.TRANSLATION))
    transport = RecordingTransport(
        queue=[
            TransportResult(
                status_code=200,
                body={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "model response text",
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 5,
                        "prompt_tokens_details": {"cached_tokens": 7},
                    },
                },
            )
        ]
    )
    client = LlmClient(transport=transport)
    request = ChatRequest(
        model=_model("ka"),
        system_prompt="stable system",
        user_prompt="chunk text",
        log_label="translation chunk-1",
    )

    with request_log_scope(
        cache,
        task_id="task-1",
        subtask_id="chunk-1",
        subtask_attempt=2,
    ):
        response = asyncio.run(client.chat(request))

    assert response.content == "model response text"
    events = cache.load_request_events("task-1")
    assert [event["status"] for event in events] == ["running", "completed"]
    assert events[0]["request_id"] == events[1]["request_id"]
    assert events[0]["phase"] == "sent"
    assert "last_activity_at" in events[0]
    assert events[0]["label"] == "translation chunk-1"
    assert events[0]["subtask_attempt"] == 2
    assert events[0]["provider_attempt"] == 1
    assert events[0]["prompt_chars"] == len("stable systemchunk text")
    assert events[1]["phase"] == "completed"
    assert events[1]["input_tokens"] == 12
    assert events[1]["output_tokens"] == 5
    assert events[1]["cached_input_tokens"] == 7
    assert events[1]["response_chars"] == len("model response text")
    assert events[1]["response_text"] == "model response text"


def test_request_log_marks_empty_model_response_failed(tmp_path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(TaskRecord(id="task-1", kind=TaskKind.TRANSLATION))
    transport = RecordingTransport(queue=[_ok("")])
    client = LlmClient(transport=transport)
    request = ChatRequest(
        model=_model("ka"),
        system_prompt="stable system",
        user_prompt="chunk text",
        log_label="translation chunk-empty",
    )

    with request_log_scope(
        cache,
        task_id="task-1",
        subtask_id="chunk-empty",
        subtask_attempt=1,
    ):
        response = asyncio.run(client.chat(request))

    assert response.content == ""
    events = cache.load_request_events("task-1")
    assert [event["status"] for event in events] == ["running", "failed"]
    assert events[1]["phase"] == "failed"
    assert events[1]["http_status"] == 200
    assert events[1]["response_chars"] == 0
    assert events[1]["error"] == "Empty model response."


def test_request_log_marks_degenerate_output_failed_with_usage(tmp_path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(TaskRecord(id="task-1", kind=TaskKind.TRANSLATION))
    runaway = "我去" * 300
    client = LlmClient(transport=RecordingTransport(queue=[_ok(runaway)]))
    request = ChatRequest(
        model=_model("ka"),
        system_prompt="stable system",
        user_prompt="chunk text",
        detect_stream_repetition=True,
        log_label="translation chunk-degenerate",
    )

    with request_log_scope(
        cache,
        task_id="task-1",
        subtask_id="chunk-degenerate",
        subtask_attempt=1,
    ):
        with pytest.raises(LlmDegenerateOutputError) as caught:
            asyncio.run(client.chat(request))

    assert caught.value.partial_response == runaway
    events = cache.load_request_events("task-1")
    assert [event["status"] for event in events] == ["running", "failed"]
    assert events[1]["error"].startswith("[llm.degenerate_output]")
    assert events[1]["response_text"] == runaway
    assert events[1]["input_tokens"] == 1
    assert events[1]["output_tokens"] == 1


@pytest.mark.parametrize(
    ("prompt", "response", "detect"),
    [
        ("translate this", "我去" * 300, False),
        ("translate " + "我去" * 300, "我去" * 300, True),
        ("translate this", "哈哈" * 100, True),
    ],
)
def test_degenerate_output_guard_avoids_non_translation_and_source_false_positives(
    prompt: str,
    response: str,
    detect: bool,
) -> None:
    client = LlmClient(transport=RecordingTransport(queue=[_ok(response)]))

    result = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model("ka"),
                system_prompt="",
                user_prompt=prompt,
                detect_stream_repetition=detect,
            )
        )
    )

    assert result.content == response


def test_request_log_records_http_failure_body(tmp_path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(TaskRecord(id="task-1", kind=TaskKind.TRANSLATION))
    transport = RecordingTransport(
        queue=[
            _http(
                500,
                {
                    "error": {
                        "code": "ModelLoading",
                        "message": "The model is currently loading.",
                    }
                },
            )
        ]
    )
    client = LlmClient(transport=transport)
    request = ChatRequest(
        model=_model("ka"),
        system_prompt="stable system",
        user_prompt="chunk text",
        log_label="translation chunk-9",
    )

    with request_log_scope(
        cache,
        task_id="task-1",
        subtask_id="chunk-9",
        subtask_attempt=1,
    ):
        with pytest.raises(LlmRequestError) as caught:
            asyncio.run(client.chat(request))

    assert caught.value.code == "llm.http_error"
    events = cache.load_request_events("task-1")
    assert [event["status"] for event in events] == ["running", "failed"]
    assert events[0]["phase"] == "sent"
    assert events[1]["phase"] == "failed"
    assert events[1]["http_status"] == 500
    assert "ModelLoading" in str(events[1]["response_text"])
    assert "stable system" not in str(events[1])
    assert "chunk text" not in str(events[1])


def test_request_log_records_local_validation_failure(tmp_path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(TaskRecord(id="task-1", kind=TaskKind.TRANSLATION))

    with request_log_scope(
        cache,
        task_id="task-1",
        subtask_id="chunk-1",
        subtask_attempt=3,
    ):
        append_local_failure(
            label="translation chunk-1 local validation",
            error="TranslationQualityFailureError: source residue",
            response_text="TranslationQualityFailureError: source residue\n\nraw model",
        )

    events = cache.load_request_events("task-1")
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["local_failure"] is True
    assert events[0]["label"] == "translation chunk-1 local validation"
    assert events[0]["subtask_attempt"] == 3
    assert "raw model" in str(events[0]["response_text"])


def test_pool_round_robins_keys_across_calls() -> None:
    transport = RecordingTransport(queue=[_ok(), _ok(), _ok()])
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        key_pool=pool,
    )

    asyncio.run(client.chat(request))
    asyncio.run(client.chat(request))
    asyncio.run(client.chat(request))

    auths = [h["Authorization"] for h in transport.headers_seen]
    assert auths == ["Bearer ka", "Bearer kb", "Bearer ka"]


def test_pool_evicts_key_on_401_and_retries_with_next() -> None:
    transport = RecordingTransport(queue=[_http(401), _ok()])
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        key_pool=pool,
    )

    asyncio.run(client.chat(request))

    auths = [h["Authorization"] for h in transport.headers_seen]
    assert auths == ["Bearer ka", "Bearer kb"]
    assert "ka" in pool.dead_keys
    assert pool.alive_count == 1


def test_pool_does_not_evict_key_on_429_but_rotates() -> None:
    transport = RecordingTransport(queue=[_http(429), _ok()])
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        key_pool=pool,
    )

    asyncio.run(client.chat(request))

    auths = [h["Authorization"] for h in transport.headers_seen]
    assert auths == ["Bearer ka", "Bearer kb"]
    assert pool.dead_keys == frozenset()


def test_pool_falls_back_when_provider_rejects_stream_options() -> None:
    transport = RecordingTransport(
        queue=[
            _http(
                400,
                {
                    "error": {
                        "message": "Unrecognized request argument supplied: stream_options"
                    }
                },
            ),
            _ok("fallback ok"),
        ]
    )
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        stream=True,
        key_pool=pool,
    )

    response = asyncio.run(client.chat(request))

    assert response.content == "fallback ok"
    assert [h["Authorization"] for h in transport.headers_seen] == [
        "Bearer ka",
        "Bearer ka",
    ]
    assert transport.payloads_seen[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in transport.payloads_seen[1]
    assert pool.dead_keys == frozenset()


def test_pool_falls_back_when_provider_rejects_thinking() -> None:
    transport = RecordingTransport(
        queue=[
            _http(
                400,
                {
                    "error": {
                        "message": "Unknown parameter: 'thinking'.",
                        "param": "thinking",
                        "code": "unknown_parameter",
                    }
                },
            ),
            _ok("fallback ok"),
        ]
    )
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        key_pool=pool,
    )

    response = asyncio.run(client.chat(request))

    assert response.content == "fallback ok"
    assert [h["Authorization"] for h in transport.headers_seen] == [
        "Bearer ka",
        "Bearer ka",
    ]
    assert transport.payloads_seen[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in transport.payloads_seen[1]
    assert pool.dead_keys == frozenset()


def test_pool_falls_back_when_provider_rejects_streaming() -> None:
    transport = RecordingTransport(
        queue=[
            _http(
                400,
                {
                    "error": {
                        "message": "Unrecognized request argument supplied: stream"
                    }
                },
            ),
            _ok("fallback ok"),
        ]
    )
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        stream=True,
        key_pool=pool,
    )

    response = asyncio.run(client.chat(request))

    assert response.content == "fallback ok"
    assert [h["Authorization"] for h in transport.headers_seen] == [
        "Bearer ka",
        "Bearer ka",
    ]
    assert transport.payloads_seen[0]["stream"] is True
    assert "stream" not in transport.payloads_seen[1]
    assert "stream_options" not in transport.payloads_seen[1]
    assert pool.dead_keys == frozenset()


def test_pool_keeps_polling_after_account_level_429() -> None:
    transport = RecordingTransport(
        queue=[
            _http(
                429,
                {
                    "error": {
                        "code": "SetLimitExceeded",
                        "message": "model service has been paused by Safe Experience Mode",
                    }
                },
            ),
            _ok(),
        ]
    )
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        key_pool=pool,
    )

    response = asyncio.run(client.chat(request))

    assert response.content == "hi"
    assert [h["Authorization"] for h in transport.headers_seen] == [
        "Bearer ka",
        "Bearer kb",
    ]
    assert pool.dead_keys == frozenset()


def test_pool_raises_all_keys_failed_when_every_key_dead() -> None:
    transport = RecordingTransport(queue=[_http(403), _http(403)])
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka", "kb"))
    request = ChatRequest(
        model=_model("ka", "kb"),
        system_prompt="",
        user_prompt="ping",
        key_pool=pool,
    )

    with pytest.raises(LlmRequestError) as caught:
        asyncio.run(client.chat(request))

    assert caught.value.code == "llm.all_keys_failed"
    assert pool.dead_keys == frozenset({"ka", "kb"})


def test_pool_request_log_records_auth_failure_body(tmp_path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.save_task(TaskRecord(id="task-1", kind=TaskKind.TRANSLATION))
    transport = RecordingTransport(
        queue=[
            _http(
                403,
                {"error": {"message": "bad key"}},
            )
        ]
    )
    client = LlmClient(transport=transport)
    pool = KeyPool(("ka",))
    request = ChatRequest(
        model=_model("ka"),
        system_prompt="stable system",
        user_prompt="chunk text",
        key_pool=pool,
    )

    with request_log_scope(
        cache,
        task_id="task-1",
        subtask_id="chunk-1",
        subtask_attempt=1,
    ):
        with pytest.raises(LlmRequestError) as caught:
            asyncio.run(client.chat(request))

    assert caught.value.code == "llm.all_keys_failed"
    events = cache.load_request_events("task-1")
    assert [event["status"] for event in events] == ["running", "failed"]
    assert events[1]["phase"] == "failed"
    assert events[1]["http_status"] == 403
    assert "bad key" in str(events[1]["response_text"])
    assert "stable system" not in str(events[1])
    assert "chunk text" not in str(events[1])
