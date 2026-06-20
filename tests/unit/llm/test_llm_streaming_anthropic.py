"""Anthropic Server-Sent-Events streaming format support."""

from __future__ import annotations

import asyncio
import json

import httpx

from transoria.llm.client import HttpxChatTransport


def test_httpx_streaming_transport_parses_anthropic_event_stream() -> None:
    sse_body = "\n".join(
        [
            "event: message_start",
            'data: {"type":"message_start","message":{"id":"m_1","usage":{"input_tokens":42,"output_tokens":0}}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"안녕"}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" 세상"}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{"output_tokens":7}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
            "data: [DONE]",
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=sse_body,
            headers={"content-type": "text/event-stream"},
        )

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
                "https://api.anthropic.com/v1/messages",
                {"x-api-key": "k", "anthropic-version": "2023-06-01"},
                {"model": "claude", "messages": [], "stream": True},
                10.0,
            )
        )
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc, assignment]

    # Anthropic-shaped body (the parser path the LlmClient will use).
    assert result.body["content"][0]["text"] == "안녕 세상"
    # Usage is merged across message_start + message_delta.
    assert result.body["usage"]["input_tokens"] == 42
    assert result.body["usage"]["output_tokens"] == 7
