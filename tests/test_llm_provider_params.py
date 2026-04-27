from __future__ import annotations

import asyncio
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
        self.captured.append(dict(payload))
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


def test_anthropic_thinking_budget_uses_model_config_value() -> None:
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
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 12000}


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
    assert payload["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 8000
