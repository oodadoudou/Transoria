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
