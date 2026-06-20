from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from transoria.llm import (
    ChatRequest,
    LlmClient,
    LlmRequestError,
    ModelConfig,
    NoApiKeyError,
    ProviderFormat,
)
from transoria.llm.client import TransportResult


@dataclass
class FakeTransport:
    responses: list[TransportResult] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        return self.responses.pop(0)


def _model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="m",
        api_keys=("k",),
    )


def test_no_api_key_error_has_stable_code() -> None:
    transport = FakeTransport()
    client = LlmClient(transport=transport)
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="m",
        api_keys=(),
    )

    with pytest.raises(NoApiKeyError) as excinfo:
        asyncio.run(client.chat(ChatRequest(model=model, system_prompt="", user_prompt="x")))

    assert excinfo.value.code == "llm.no_api_key"


def test_http_error_has_stable_code() -> None:
    transport = FakeTransport(responses=[TransportResult(401, {"error": "bad"})])
    client = LlmClient(transport=transport)

    with pytest.raises(LlmRequestError) as excinfo:
        asyncio.run(client.chat(ChatRequest(model=_model(), system_prompt="", user_prompt="x")))

    assert excinfo.value.code == "llm.http_error"


def test_default_code_is_present_on_plain_llm_request_error() -> None:
    err = LlmRequestError("something went wrong")
    assert err.code == "llm.error"


def test_explicit_code_overrides_default() -> None:
    err = LlmRequestError("custom", code="llm.line_count_mismatch")
    assert err.code == "llm.line_count_mismatch"
