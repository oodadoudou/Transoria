from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from transoria.llm import (
    ChatRequest,
    LlmClient,
    LlmDegenerateOutputError,
    LlmRequestError,
    ModelConfig,
    ProviderFormat,
    ThinkingLevel,
)
from transoria.llm.client import TransportResult


@dataclass
class StreamingFakeTransport:
    """Records that stream=True is plumbed through and returns content as if streamed."""

    streamed_content: str = ""
    captured: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.captured.append(dict(payload))
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": self.streamed_content}}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        }
        return TransportResult(status_code=200, body=body)


def _model(**overrides: object) -> ModelConfig:
    values: dict[str, object] = {
        "id": "m",
        "display_name": "m",
        "provider_format": ProviderFormat.OPENAI,
        "base_url": "https://example/api/v1/",
        "model_id": "m",
        "api_keys": ("k",),
    }
    values.update(overrides)
    return ModelConfig(**values)  # type: ignore[arg-type]


def test_chat_request_carries_stream_flag_to_payload() -> None:
    transport = StreamingFakeTransport(streamed_content="hi")
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="", user_prompt="x", stream=True)
        )
    )

    assert transport.captured[0]["stream"] is True
    assert transport.captured[0]["stream_options"] == {"include_usage": True}


def test_chat_omits_stream_flag_when_false() -> None:
    transport = StreamingFakeTransport(streamed_content="hi")
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="", user_prompt="x")
        )
    )

    assert "stream" not in transport.captured[0]


def test_streaming_response_accumulates_into_chat_response() -> None:
    """With a transport that synthesizes accumulated content, the LlmClient
    returns a single ChatResponse — callers don't have to know whether the
    underlying call was streamed."""

    transport = StreamingFakeTransport(streamed_content="hello world")
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="", user_prompt="x", stream=True)
        )
    )

    assert response.content == "hello world"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 22


def test_custom_streaming_request_includes_usage_options() -> None:
    transport = StreamingFakeTransport(streamed_content="hi")
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(provider_format=ProviderFormat.CUSTOM),
                system_prompt="",
                user_prompt="x",
                stream=True,
            )
        )
    )

    assert transport.captured[0]["stream"] is True
    assert transport.captured[0]["stream_options"] == {"include_usage": True}


def test_sakura_streaming_request_omits_usage_options() -> None:
    transport = StreamingFakeTransport(streamed_content="hi")
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(provider_format=ProviderFormat.SAKURA),
                system_prompt="",
                user_prompt="x",
                stream=True,
            )
        )
    )

    assert transport.captured[0]["stream"] is True
    assert "stream_options" not in transport.captured[0]


def test_streaming_usage_options_fall_back_when_provider_rejects_field() -> None:
    transport = StreamingFakeTransport(streamed_content="hi")
    transport.execute = _reject_stream_options_once_then_succeed(transport)  # type: ignore[method-assign]
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="", user_prompt="x", stream=True)
        )
    )

    assert response.content == "hi"
    assert len(transport.captured) == 2
    assert transport.captured[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in transport.captured[1]


def test_streaming_usage_options_do_not_fall_back_for_unrelated_400() -> None:
    class RejectingTransport:
        calls: list[dict[str, object]]

        def __init__(self) -> None:
            self.calls = []

        async def execute(
            self,
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, object],
            timeout: float,
        ) -> TransportResult:
            self.calls.append(dict(payload))
            return TransportResult(status_code=400, body={"error": "bad prompt"})

    transport = RejectingTransport()
    client = LlmClient(transport=transport)

    try:
        asyncio.run(
            client.chat(
                ChatRequest(
                    model=_model(),
                    system_prompt="",
                    user_prompt="x",
                    stream=True,
                )
            )
        )
    except LlmRequestError:
        pass
    else:
        raise AssertionError("Expected LlmRequestError")

    assert len(transport.calls) == 1
    assert transport.calls[0]["stream_options"] == {"include_usage": True}


def test_streaming_falls_back_when_provider_rejects_streaming() -> None:
    transport = StreamingFakeTransport(streamed_content="hi")
    transport.execute = _reject_stream_once_then_succeed(transport)  # type: ignore[method-assign]
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="", user_prompt="x", stream=True)
        )
    )

    assert response.content == "hi"
    assert len(transport.captured) == 2
    assert transport.captured[0]["stream"] is True
    assert "stream" not in transport.captured[1]
    assert "stream_options" not in transport.captured[1]


def test_streaming_does_not_fall_back_for_runtime_stream_errors() -> None:
    class RejectingTransport:
        calls: list[dict[str, object]]

        def __init__(self) -> None:
            self.calls = []

        async def execute(
            self,
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, object],
            timeout: float,
        ) -> TransportResult:
            self.calls.append(dict(payload))
            return TransportResult(
                status_code=400,
                body={"error": "upstream event stream interrupted"},
            )

    transport = RejectingTransport()
    client = LlmClient(transport=transport)

    try:
        asyncio.run(
            client.chat(
                ChatRequest(
                    model=_model(),
                    system_prompt="",
                    user_prompt="x",
                    stream=True,
                )
            )
        )
    except LlmRequestError:
        pass
    else:
        raise AssertionError("Expected LlmRequestError")

    assert len(transport.calls) == 1
    assert transport.calls[0]["stream"] is True


def _reject_stream_options_once_then_succeed(transport: StreamingFakeTransport):
    call_count = 0

    async def execute(
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        nonlocal call_count
        call_count += 1
        transport.captured.append(dict(payload))
        if call_count == 1:
            return TransportResult(
                status_code=400,
                body={
                    "error": {
                        "message": "Unrecognized request argument supplied: stream_options"
                    }
                },
            )
        return TransportResult(
            status_code=200,
            body={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": transport.streamed_content,
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 22},
            },
        )

    return execute


def _reject_stream_once_then_succeed(transport: StreamingFakeTransport):
    call_count = 0

    async def execute(
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        nonlocal call_count
        call_count += 1
        transport.captured.append(dict(payload))
        if call_count == 1:
            return TransportResult(
                status_code=400,
                body={
                    "error": {
                        "message": "Unrecognized request argument supplied: stream"
                    }
                },
            )
        return TransportResult(
            status_code=200,
            body={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": transport.streamed_content,
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 22},
            },
        )

    return execute


# HttpxChatTransport SSE accumulation (uses MockTransport so no real network).


def test_httpx_streaming_transport_accumulates_sse_chunks() -> None:
    import httpx

    from transoria.llm.client import HttpxChatTransport

    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content.decode("utf-8")))
        body = (
            'data: {"choices":[{"delta":{"content":"hello "}}]}\n'
            'data: {"choices":[{"delta":{"content":"world"}}]}\n'
            'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":3,"completion_tokens":5}}\n'
            "data: [DONE]\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    real_async_client = httpx.AsyncClient

    class _PatchedClient(real_async_client):  # type: ignore[misc]
        def __init__(self, *args: object, **kwargs: object) -> None:  # noqa: ANN401
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    @dataclass
    class _ProgressLog:
        events: list[dict[str, object]] = field(default_factory=list)

        def progress(self, **payload: object) -> None:
            self.events.append(dict(payload))

    httpx.AsyncClient = _PatchedClient  # type: ignore[misc, assignment]
    try:
        transport = HttpxChatTransport()
        request_log = _ProgressLog()
        result = asyncio.run(
            transport.execute_observed(
                "https://example/api/v1/chat/completions",
                {"Authorization": "Bearer k", "Content-Type": "application/json"},
                {"model": "x", "messages": [], "stream": True},
                10.0,
                request_log=request_log,  # type: ignore[arg-type]
            )
        )
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc, assignment]

    assert result.status_code == 200
    content = result.body["choices"][0]["message"]["content"]
    assert content == "hello world"
    assert result.body.get("usage", {}).get("prompt_tokens") == 3
    assert captured_payloads[0]["stream"] is True
    phases = [event["phase"] for event in request_log.events]
    assert phases[:2] == ["headers_received", "first_token"]
    assert request_log.events[0]["status_code"] == 200
    assert request_log.events[1]["response_text"] == "hello "
    assert request_log.events[1]["response_chars"] == 6


def test_httpx_streaming_transport_aborts_runaway_repetition_early() -> None:
    import httpx

    from transoria.llm.client import HttpxChatTransport

    detected_prefix = "我去" * 300
    unread_suffix = "我去" * 3000

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            f'data: {json.dumps({"choices": [{"delta": {"content": detected_prefix}}]})}\n'
            f'data: {json.dumps({"choices": [{"delta": {"content": unread_suffix}}]})}\n'
            "data: [DONE]\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    real_async_client = httpx.AsyncClient

    class _PatchedClient(real_async_client):  # type: ignore[misc]
        def __init__(self, *args: object, **kwargs: object) -> None:  # noqa: ANN401
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    @dataclass
    class _RequestLog:
        failures: list[dict[str, object]] = field(default_factory=list)

        def progress(self, **payload: object) -> None:
            pass

        def fail(self, **payload: object) -> None:
            self.failures.append(dict(payload))

    httpx.AsyncClient = _PatchedClient  # type: ignore[misc, assignment]
    try:
        request_log = _RequestLog()
        with pytest.raises(LlmDegenerateOutputError) as caught:
            asyncio.run(
                HttpxChatTransport().execute_observed(
                    "https://example/api/v1/chat/completions",
                    {"Authorization": "Bearer k", "Content-Type": "application/json"},
                    {
                        "model": "x",
                        "messages": [{"role": "user", "content": "translate this"}],
                        "stream": True,
                    },
                    10.0,
                    request_log=request_log,  # type: ignore[arg-type]
                    detect_stream_repetition=True,
                )
            )
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc, assignment]

    assert caught.value.code == "llm.degenerate_output"
    assert caught.value.partial_response == detected_prefix
    assert unread_suffix not in caught.value.partial_response
    assert caught.value.usage.estimated is True
    assert caught.value.usage.output_tokens > 0
    assert len(request_log.failures) == 1
    assert request_log.failures[0]["response_text"] == detected_prefix
    assert request_log.failures[0]["usage"] == caught.value.usage


def test_httpx_streaming_transport_estimates_usage_when_stream_omits_usage() -> None:
    import httpx

    from transoria.llm.client import HttpxChatTransport

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"content":"translated "}}]}\n'
            'data: {"choices":[{"delta":{"content":"text"}}]}\n'
            'data: {"usage":{}}\n'
            "data: [DONE]\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    real_async_client = httpx.AsyncClient

    class _PatchedClient(real_async_client):  # type: ignore[misc]
        def __init__(self, *args: object, **kwargs: object) -> None:  # noqa: ANN401
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    httpx.AsyncClient = _PatchedClient  # type: ignore[misc, assignment]
    try:
        transport = HttpxChatTransport()
        result = asyncio.run(
            transport.execute(
                "https://example/api/v1/chat/completions",
                {"Authorization": "Bearer k", "Content-Type": "application/json"},
                {
                    "model": "x",
                    "messages": [{"role": "user", "content": "source text"}],
                    "stream": True,
                },
                10.0,
            )
        )
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc, assignment]

    usage = result.body.get("usage", {})
    assert usage.get("transoria_estimated") is True
    assert usage.get("prompt_tokens", 0) > 0
    assert usage.get("completion_tokens", 0) > 0
    assert result.body.get("usageMetadata", {}).get("transoriaEstimated") is True
