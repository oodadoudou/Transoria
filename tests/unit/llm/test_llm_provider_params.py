from __future__ import annotations

import asyncio
import copy
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
class FakeTransport:
    responses: list[TransportResult] = field(default_factory=list)
    captured: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.captured.append(copy.deepcopy(dict(payload)))
        return self.responses.pop(0)


def test_anthropic_max_tokens_uses_model_config_value() -> None:
    body = {
        "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.ANTHROPIC,
        base_url="https://api.anthropic.com",
        model_id="claude-3-5-sonnet",
        api_keys=("k",),
        max_output_tokens=8192,
    )
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(ChatRequest(model=model, system_prompt="", user_prompt="x"))
    )

    assert transport.captured[0]["max_tokens"] == 8192


def test_anthropic_max_tokens_zero_falls_back_to_safe_minimum() -> None:
    """Anthropic's /v1/messages requires non-zero ``max_tokens``;
    sending 0 is rejected. When the user leaves max_output_tokens
    unset / 0, substitute a safe minimum (~8K) so the API call
    still goes through."""

    body = {
        "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.ANTHROPIC,
        base_url="https://api.anthropic.com",
        model_id="claude-3-5-sonnet",
        api_keys=("k",),
        max_output_tokens=0,
    )
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(ChatRequest(model=model, system_prompt="", user_prompt="x"))
    )

    sent = transport.captured[0]["max_tokens"]
    assert isinstance(sent, int) and sent >= 4096


def test_anthropic_thinking_budget_uses_level_aware_value() -> None:
    body = {
        "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.ANTHROPIC,
        base_url="https://api.anthropic.com",
        model_id="claude-3-5-sonnet",
        api_keys=("k",),
        thinking_level=ThinkingLevel.HIGH,
        thinking_budget_tokens=12000,
    )
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(ChatRequest(model=model, system_prompt="", user_prompt="x"))
    )

    payload = transport.captured[0]
    # HIGH maps to 1024 by the level ladder; the user's 12000 only acts
    # as an upper bound. Without the ladder, sending 12000 directly
    # caused 4-12x extra output cost on real translations.
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1024}


def test_google_generation_config_includes_thinking_when_enabled() -> None:
    body = {
        "candidates": [{"content": {"parts": [{"text": "x"}]}}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.GOOGLE,
        base_url="https://generativelanguage.googleapis.com",
        model_id="gemini-2.5-pro",
        api_keys=("k",),
        thinking_level=ThinkingLevel.MEDIUM,
        thinking_budget_tokens=8000,
    )
    client = LlmClient(transport=transport)

    asyncio.run(
        client.chat(ChatRequest(model=model, system_prompt="", user_prompt="x"))
    )

    payload = transport.captured[0]
    # MEDIUM maps to 768 by the level ladder; the user's 8000 only
    # acts as an upper bound.
    assert payload["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 768


def test_anthropic_retries_without_unsupported_cache_control() -> None:
    ok_body = {
        "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    transport = FakeTransport(
        responses=[
            TransportResult(
                400,
                {
                    "error": {
                        "message": "cache_control: Extra inputs are not permitted"
                    }
                },
            ),
            TransportResult(200, ok_body),
        ]
    )
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.ANTHROPIC,
        base_url="https://anthropic-compatible.example",
        model_id="claude-compatible",
        api_keys=("k",),
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(ChatRequest(model=model, system_prompt="sys", user_prompt="x"))
    )

    assert response.content == "x"
    assert transport.captured[0]["system"][0]["cache_control"] == {
        "type": "ephemeral"
    }
    assert "cache_control" not in transport.captured[1]["system"][0]
    assert transport.captured[1]["system"][0]["text"] == "sys"


def test_google_retries_without_unsupported_thinking_config() -> None:
    ok_body = {
        "candidates": [{"content": {"parts": [{"text": "x"}]}}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    transport = FakeTransport(
        responses=[
            TransportResult(
                400,
                {
                    "error": {
                        "message": (
                            "Unknown name 'thinkingConfig' at "
                            "'generation_config'"
                        )
                    }
                },
            ),
            TransportResult(200, ok_body),
        ]
    )
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.GOOGLE,
        base_url="https://generativelanguage.googleapis.com",
        model_id="gemini-compatible",
        api_keys=("k",),
        thinking_level=ThinkingLevel.MEDIUM,
        thinking_budget_tokens=8000,
    )
    client = LlmClient(transport=transport)

    response = asyncio.run(
        client.chat(ChatRequest(model=model, system_prompt="sys", user_prompt="x"))
    )

    assert response.content == "x"
    assert "thinkingConfig" in transport.captured[0]["generationConfig"]
    assert "generationConfig" not in transport.captured[1]


def test_google_history_maps_assistant_role_to_model() -> None:
    from transoria.llm.client import ChatMessage

    body = {
        "candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.GOOGLE,
        base_url="https://generativelanguage.googleapis.com",
        model_id="gemini-2.5-flash",
        api_keys=("k",),
    )

    asyncio.run(
        LlmClient(transport=transport).chat(
            ChatRequest(
                model=model,
                system_prompt="",
                user_prompt="continue",
                history=(
                    ChatMessage(role="user", content="q"),
                    ChatMessage(role="assistant", content="partial"),
                ),
            )
        )
    )

    assert [c["role"] for c in transport.captured[0]["contents"]] == ["user", "model", "user"]


def test_google_usage_counts_thinking_tokens_as_output() -> None:
    body = {
        "candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 4,
            "thoughtsTokenCount": 30,
        },
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.GOOGLE,
        base_url="https://generativelanguage.googleapis.com",
        model_id="gemini-2.5-flash",
        api_keys=("k",),
        thinking_level=ThinkingLevel.LOW,
    )

    response = asyncio.run(
        LlmClient(transport=transport).chat(
            ChatRequest(model=model, system_prompt="", user_prompt="x")
        )
    )

    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 34


def _google_model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.GOOGLE,
        base_url="https://generativelanguage.googleapis.com",
        model_id="gemini-2.5-flash",
        api_keys=("k",),
    )


def test_google_prohibited_content_finish_is_a_filtered_response() -> None:
    import pytest

    from transoria.llm.client import LlmFilteredResponseError

    body = {
        "candidates": [{"content": {"parts": [{"text": "部分"}]}, "finishReason": "PROHIBITED_CONTENT"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    transport = FakeTransport(responses=[TransportResult(200, body)])

    with pytest.raises(LlmFilteredResponseError) as raised:
        asyncio.run(
            LlmClient(transport=transport).chat(
                ChatRequest(model=_google_model(), system_prompt="", user_prompt="x")
            )
        )
    assert raised.value.partial_response == "部分"


def test_google_blocked_prompt_reports_content_filter() -> None:
    import pytest

    from transoria.llm.client import LlmRequestError

    body = {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
    transport = FakeTransport(responses=[TransportResult(200, body)])

    with pytest.raises(LlmRequestError) as raised:
        asyncio.run(
            LlmClient(transport=transport).chat(
                ChatRequest(model=_google_model(), system_prompt="", user_prompt="x")
            )
        )
    assert raised.value.code == "llm.content_filter"
    assert "PROHIBITED_CONTENT" in str(raised.value)


def test_explicit_request_output_cap_reaches_openai_and_google_payloads() -> None:
    openai_body = {
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    google_body = {
        "candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    openai_model = ModelConfig(
        id="o", display_name="o", provider_format=ProviderFormat.OPENAI,
        base_url="https://api.example.com/v1", model_id="m", api_keys=("k",),
    )
    transport = FakeTransport(
        responses=[TransportResult(200, openai_body), TransportResult(200, openai_body),
                   TransportResult(200, google_body), TransportResult(200, google_body)]
    )
    client = LlmClient(transport=transport)

    async def scenario() -> None:
        for model in (openai_model, _google_model()):
            await client.chat(ChatRequest(model=model, system_prompt="", user_prompt="x"))
            await client.chat(
                ChatRequest(model=model, system_prompt="", user_prompt="x", max_output_tokens=8192)
            )

    asyncio.run(scenario())

    plain_openai, capped_openai, plain_google, capped_google = transport.captured
    assert "max_tokens" not in plain_openai
    assert capped_openai["max_tokens"] == 8192
    assert "maxOutputTokens" not in plain_google["generationConfig"]
    assert capped_google["generationConfig"]["maxOutputTokens"] == 8192
