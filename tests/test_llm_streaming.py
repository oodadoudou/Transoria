from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Mapping

from transoria.llm import (
    ChatRequest,
    LlmClient,
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


def _model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="m",
        api_keys=("k",),
    )


def test_chat_request_carries_stream_flag_to_payload() -> None:
    transport = StreamingFakeTransport(streamed_content="hi")
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="", user_prompt="x", stream=True)
        )
    )

    assert transport.captured[0]["stream"] is True


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


# ---------------------------------------------------------------------------
# HttpxChatTransport SSE accumulation (uses MockTransport so no real network).
# ---------------------------------------------------------------------------


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

    httpx.AsyncClient = _PatchedClient  # type: ignore[misc, assignment]
    try:
        transport = HttpxChatTransport()
        result = asyncio.run(
            transport.execute(
                "https://example/api/v1/chat/completions",
                {"Authorization": "Bearer k", "Content-Type": "application/json"},
                {"model": "x", "messages": [], "stream": True},
                10.0,
            )
        )
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc, assignment]

    assert result.status_code == 200
    content = result.body["choices"][0]["message"]["content"]
    assert content == "hello world"
    assert result.body.get("usage", {}).get("prompt_tokens") == 3
    assert captured_payloads[0]["stream"] is True
