from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from transoria.llm import (
    ChatRequest,
    LlmClient,
    LlmRequestError,
    LlmTruncatedResponseError,
    ModelConfig,
    NoApiKeyError,
    ProviderFormat,
    ThinkingLevel,
)
from transoria.llm.client import ChatTransport, TransportResult


@dataclass
class FakeTransport:
    """Pluggable :class:`ChatTransport` for unit tests."""

    responses: list[TransportResult] = field(default_factory=list)
    raise_on_call: list[Exception | None] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout": timeout,
            }
        )
        if self.raise_on_call:
            err = self.raise_on_call.pop(0)
            if err is not None:
                raise err
        return self.responses.pop(0)


def _model(**overrides: object) -> ModelConfig:
    base: dict[str, object] = dict(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("k1",),
        thinking_level=ThinkingLevel.OFF,
        timeout_seconds=10.0,
        concurrency_limit=2,
        rpm_limit=60,
        rotate_keys=True,
    )
    base.update(overrides)
    return ModelConfig(**base)  # type: ignore[arg-type]


def _ok_body(content: str = "hello") -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22},
    }


def test_chat_returns_content_and_usage() -> None:
    transport = FakeTransport(responses=[TransportResult(200, _ok_body("ok"))])
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="sys", user_prompt="user")
        )
    )

    assert response.content == "ok"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 22
    assert response.usage.total_tokens == 33


def test_chat_uses_chat_completions_endpoint_with_messages_and_auth_header() -> None:
    transport = FakeTransport(responses=[TransportResult(200, _ok_body())])
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(api_keys=("secret",)),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    call = transport.calls[0]
    assert call["url"] == "https://example/api/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret"
    payload = call["payload"]
    assert payload["model"] == "model-x"
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    # ``thinking_level=OFF`` (the ``_model`` default) always serializes
    # to an explicit ``{"type": "disabled"}`` so reasoning-default
    # providers honor the user's intent. Non-reasoning providers ignore
    # the field.
    assert payload["thinking"] == {"type": "disabled"}


def test_chat_includes_thinking_payload_when_thinking_enabled() -> None:
    transport = FakeTransport(responses=[TransportResult(200, _ok_body())])
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(thinking_level=ThinkingLevel.HIGH),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    payload = transport.calls[0]["payload"]
    # ``effort`` is intentionally omitted: it forces the most expensive
    # reasoning tier (4x token cost) without a quality win on
    # translation. Provider applies its own default thinking budget.
    assert payload["thinking"] == {"type": "enabled"}


def test_chat_explicitly_disables_thinking_when_level_is_off() -> None:
    """``thinking_level=OFF`` always serializes as
    ``thinking={"type": "disabled"}`` — model-id agnostic. Reasoning-
    default providers (DeepSeek-V3.2, GLM-Zero, Kimi-K, etc.) honor
    this; non-reasoning providers ignore the unknown body field."""

    transport = FakeTransport(responses=[TransportResult(200, _ok_body())])
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(thinking_level=ThinkingLevel.OFF),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    payload = transport.calls[0]["payload"]
    assert payload["thinking"] == {"type": "disabled"}


def test_chat_retries_without_unsupported_thinking_and_remembers_capability() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(
                400,
                {
                    "error": {
                        "message": "Unknown parameter: 'thinking'.",
                        "type": "invalid_request_error",
                        "param": "thinking",
                        "code": "unknown_parameter",
                    }
                },
            ),
            TransportResult(200, _ok_body("first")),
            TransportResult(200, _ok_body("second")),
        ]
    )
    client = LlmClient(transport=transport)
    request = ChatRequest(model=_model(), system_prompt="sys", user_prompt="user")

    first = asyncio.run(client.chat(request))
    second = asyncio.run(client.chat(request))

    assert first.content == "first"
    assert second.content == "second"
    assert transport.calls[0]["payload"]["thinking"] == {"type": "disabled"}
    assert "thinking" not in transport.calls[1]["payload"]
    assert "thinking" not in transport.calls[2]["payload"]


def test_chat_retries_without_thinking_when_enabled_model_rejects_parameter() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(
                422,
                {
                    "detail": [
                        {
                            "loc": ["body", "thinking"],
                            "msg": "Extra inputs are not permitted",
                        }
                    ]
                },
            ),
            TransportResult(200, _ok_body("ok")),
        ]
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(thinking_level=ThinkingLevel.HIGH),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    assert response.content == "ok"
    assert transport.calls[0]["payload"]["thinking"] == {"type": "enabled"}
    assert "thinking" not in transport.calls[1]["payload"]


def test_chat_does_not_drop_thinking_for_unrelated_bad_request() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(
                400,
                {
                    "error": {
                        "message": "The thinking budget is invalid.",
                        "type": "invalid_request_error",
                        "param": "thinking",
                        "code": "invalid_value",
                    }
                },
            )
        ]
    )
    client = LlmClient(transport=transport)

    with pytest.raises(LlmRequestError, match="HTTP 400"):
        asyncio.run(
            client.chat(
                ChatRequest(
                    model=_model(thinking_level=ThinkingLevel.HIGH),
                    system_prompt="sys",
                    user_prompt="user",
                )
            )
        )

    assert len(transport.calls) == 1
    assert transport.calls[0]["payload"]["thinking"] == {"type": "enabled"}


def test_chat_retries_without_unsupported_sampling_parameter() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(
                400,
                {
                    "error": {
                        "message": (
                            "Unsupported value: 'temperature' does not support "
                            "0.3 with this model."
                        ),
                        "param": "temperature",
                        "code": "unsupported_value",
                    }
                },
            ),
            TransportResult(200, _ok_body("ok")),
        ]
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(temperature=0.3),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    assert response.content == "ok"
    assert transport.calls[0]["payload"]["temperature"] == 0.3
    assert "temperature" not in transport.calls[1]["payload"]
    assert transport.calls[1]["payload"]["thinking"] == {"type": "disabled"}


def test_successful_content_cannot_trigger_payload_fallback() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body("Unknown parameter: thinking")),
        ]
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="sys", user_prompt="user")
        )
    )

    assert response.content == "Unknown parameter: thinking"
    assert len(transport.calls) == 1


def test_chat_applies_sequential_openai_compatibility_fallbacks() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(
                400,
                {"error": {"message": "Unknown parameter: stream_options"}},
            ),
            TransportResult(
                400,
                {"error": {"message": "Unknown parameter: thinking"}},
            ),
            TransportResult(
                400,
                {"error": {"message": "Streaming is not supported"}},
            ),
            TransportResult(200, _ok_body("ok")),
        ]
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(),
                system_prompt="sys",
                user_prompt="user",
                stream=True,
            )
        )
    )

    assert response.content == "ok"
    assert "stream_options" in transport.calls[0]["payload"]
    assert "stream_options" not in transport.calls[1]["payload"]
    assert "thinking" in transport.calls[1]["payload"]
    assert "thinking" not in transport.calls[2]["payload"]
    assert "stream" in transport.calls[2]["payload"]
    assert "stream" not in transport.calls[3]["payload"]


def test_chat_rotates_api_key_on_429() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(429, {"error": "rate limited"}),
            TransportResult(200, _ok_body("ok")),
        ]
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(api_keys=("first", "second"), rotate_keys=True),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    assert response.content == "ok"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer first"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer second"


def test_chat_rotates_account_level_429_like_transient_rate_limit() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(
                429,
                {
                    "error": {
                        "code": "SetLimitExceeded",
                        "message": "model service has been paused by Safe Experience Mode",
                    }
                },
            ),
            TransportResult(200, _ok_body("ok")),
        ]
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(api_keys=("first", "second"), rotate_keys=True),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    assert response.content == "ok"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer first"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer second"


def test_chat_raises_when_all_keys_fail_with_401() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(401, {"error": "unauthorized"}),
            TransportResult(401, {"error": "unauthorized"}),
        ]
    )
    client = LlmClient(transport=transport)

    with pytest.raises(LlmRequestError, match="HTTP 401"):
        asyncio.run(
            client.chat(
                ChatRequest(
                    model=_model(api_keys=("a", "b"), rotate_keys=True),
                    system_prompt="",
                    user_prompt="user",
                )
            )
        )


def test_chat_raises_no_api_key_error_when_keys_empty() -> None:
    transport = FakeTransport(responses=[])
    client = LlmClient(transport=transport)

    with pytest.raises(NoApiKeyError):
        asyncio.run(
            client.chat(
                ChatRequest(
                    model=_model(api_keys=()),
                    system_prompt="",
                    user_prompt="user",
                )
            )
        )


def test_chat_propagates_cancelled_error() -> None:
    class CancellingTransport:
        async def execute(self, url, headers, payload, timeout):  # type: ignore[no-untyped-def]
            raise asyncio.CancelledError()

    client = LlmClient(transport=CancellingTransport())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.chat(
                ChatRequest(
                    model=_model(), system_prompt="", user_prompt="user"
                )
            )
        )


def test_chat_transport_error_includes_exception_class_when_message_empty() -> None:
    class EmptyTransportError(Exception):
        def __str__(self) -> str:
            return ""

    transport = FakeTransport(raise_on_call=[EmptyTransportError()])
    client = LlmClient(transport=transport)

    with pytest.raises(LlmRequestError) as caught:
        asyncio.run(
            client.chat(
                ChatRequest(
                    model=_model(rotate_keys=False),
                    system_prompt="",
                    user_prompt="user",
                )
            )
        )

    assert caught.value.code == "llm.transport_error"
    assert str(caught.value).endswith(": EmptyTransportError")


def test_chat_skips_empty_system_prompt() -> None:
    transport = FakeTransport(responses=[TransportResult(200, _ok_body())])
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="", user_prompt="user-only")
        )
    )

    payload = transport.calls[0]["payload"]
    assert payload["messages"] == [{"role": "user", "content": "user-only"}]


def test_chat_routes_anthropic_provider_to_messages_endpoint() -> None:
    body = {
        "content": [{"type": "text", "text": "안녕"}],
        "usage": {
            "input_tokens": 7,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 5,
            "output_tokens": 3,
        },
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(provider_format=ProviderFormat.ANTHROPIC),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    assert response.content == "안녕"
    assert response.usage.input_tokens == 32
    assert response.usage.cached_input_tokens == 20
    assert transport.calls[0]["url"].endswith("/v1/messages")
    assert transport.calls[0]["headers"]["x-api-key"]
    payload = transport.calls[0]["payload"]
    assert payload["system"] == [
        {
            "type": "text",
            "text": "sys",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert payload["messages"][-1]["content"] == "user"


def test_chat_omits_anthropic_system_when_empty() -> None:
    body = {
        "content": [{"type": "text", "text": "안녕"}],
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(provider_format=ProviderFormat.ANTHROPIC),
                system_prompt="",
                user_prompt="user",
            )
        )
    )

    assert "system" not in transport.calls[0]["payload"]


def test_chat_routes_google_provider_to_generate_content_endpoint() -> None:
    body = {
        "candidates": [
            {"content": {"parts": [{"text": "ola"}]}}
        ],
        "usageMetadata": {
            "promptTokenCount": 5,
            "candidatesTokenCount": 4,
            "cachedContentTokenCount": 3,
        },
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(provider_format=ProviderFormat.GOOGLE),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    assert response.content == "ola"
    assert response.usage.total_tokens == 9
    assert response.usage.cached_input_tokens == 3
    assert ":generateContent?key=" in transport.calls[0]["url"]


def test_google_response_skips_thought_parts() -> None:
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "private reasoning", "thought": True},
                        {"text": '{"0":"hola"}\n'},
                    ]
                }
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 4},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(provider_format=ProviderFormat.GOOGLE),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    assert response.content == '{"0":"hola"}\n'


def test_chat_includes_sampling_overrides_in_openai_payload() -> None:
    body = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(
                    top_p=0.9,
                    temperature=0.4,
                    presence_penalty=0.2,
                    frequency_penalty=0.1,
                    custom_headers=(("X-Trace", "abc"),),
                ),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    payload = transport.calls[0]["payload"]
    assert payload["top_p"] == 0.9
    assert payload["temperature"] == 0.4
    assert payload["presence_penalty"] == 0.2
    assert payload["frequency_penalty"] == 0.1
    assert transport.calls[0]["headers"]["X-Trace"] == "abc"


def test_chat_omits_sampling_overrides_when_unset() -> None:
    body = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(
            ChatRequest(
                model=_model(),
                system_prompt="sys",
                user_prompt="user",
            )
        )
    )

    payload = transport.calls[0]["payload"]
    assert "top_p" not in payload
    assert "temperature" not in payload
    assert "presence_penalty" not in payload
    assert "frequency_penalty" not in payload


def _body_with_finish_reason(
    content: str = "hello", finish_reason: str = "stop"
) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22},
    }


def test_chat_passes_through_normal_finish_reason() -> None:
    transport = FakeTransport(
        responses=[TransportResult(200, _body_with_finish_reason("ok", "stop"))]
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(
            ChatRequest(model=_model(), system_prompt="sys", user_prompt="user")
        )
    )

    assert response.finish_reason == "stop"
    assert response.content == "ok"


def test_chat_raises_on_content_filter_finish_reason() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _body_with_finish_reason("partial", "content_filter"))
        ]
    )
    client = LlmClient(transport=transport)

    with pytest.raises(LlmRequestError) as exc_info:
        asyncio.run(
            client.chat(
                ChatRequest(model=_model(), system_prompt="sys", user_prompt="user")
            )
        )

    assert exc_info.value.code == "llm.content_filter"


def test_chat_raises_on_length_finish_reason() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _body_with_finish_reason("partial", "length"))
        ]
    )
    client = LlmClient(transport=transport)

    with pytest.raises(LlmRequestError) as exc_info:
        asyncio.run(
            client.chat(
                ChatRequest(model=_model(), system_prompt="sys", user_prompt="user")
            )
        )

    assert exc_info.value.code == "llm.length_truncated"
    assert isinstance(exc_info.value, LlmTruncatedResponseError)
    assert exc_info.value.partial_response == "partial"
    assert exc_info.value.usage.input_tokens == 11
    assert exc_info.value.usage.output_tokens == 22
    assert exc_info.value.finish_reason == "length"
