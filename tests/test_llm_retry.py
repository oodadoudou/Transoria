from __future__ import annotations

import asyncio
import json

import pytest

from transoria.llm import LlmRequestError, NoApiKeyError, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.retry import is_transient_llm_error, retry_async


def _model(retry_attempts: int = 2) -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("k",),
        thinking_level=ThinkingLevel.OFF,
        retry_attempts=retry_attempts,
        retry_initial_backoff_seconds=0.0,
        retry_max_backoff_seconds=0.0,
    )


def test_is_transient_classifies_timeouts_and_5xx_as_retryable() -> None:
    assert is_transient_llm_error(asyncio.TimeoutError())
    assert is_transient_llm_error(TimeoutError())
    assert is_transient_llm_error(LlmRequestError("HTTP 503 from x"))
    assert is_transient_llm_error(LlmRequestError("HTTP 500"))
    assert is_transient_llm_error(json.JSONDecodeError("e", "doc", 0))


def test_is_transient_does_not_retry_4xx_or_no_api_key() -> None:
    assert not is_transient_llm_error(LlmRequestError("HTTP 401 from x"))
    assert not is_transient_llm_error(LlmRequestError("HTTP 400"))
    assert not is_transient_llm_error(NoApiKeyError("no keys"))
    assert not is_transient_llm_error(asyncio.CancelledError())


def test_is_transient_retries_translation_line_count_mismatch() -> None:
    assert is_transient_llm_error(
        LlmRequestError("Translation line count mismatch — expected [0,1] got [0]")
    )


def test_retry_async_succeeds_without_retry_when_first_attempt_works() -> None:
    calls: list[int] = []

    async def op() -> str:
        calls.append(1)
        return "ok"

    result = asyncio.run(retry_async(op, model=_model(retry_attempts=2)))

    assert result == "ok"
    assert len(calls) == 1


def test_retry_async_retries_on_transient_then_succeeds() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        if state["calls"] < 3:
            raise LlmRequestError("HTTP 503 from x")
        return "finally"

    result = asyncio.run(
        retry_async(
            op,
            model=ModelConfig(
                id="m",
                display_name="m",
                provider_format=ProviderFormat.OPENAI,
                base_url="https://x/v1/",
                model_id="m",
                api_keys=("k",),
                retry_attempts=3,
                retry_initial_backoff_seconds=0.5,
                retry_max_backoff_seconds=2.0,
            ),
            sleep=fake_sleep,
        )
    )

    assert result == "finally"
    assert state["calls"] == 3
    # Two backoffs occurred; doubling each time, capped at max.
    assert sleeps == [0.5, 1.0]


def test_retry_async_raises_after_exhausting_attempts() -> None:
    async def op() -> str:
        raise LlmRequestError("HTTP 503")

    with pytest.raises(LlmRequestError, match="HTTP 503"):
        asyncio.run(retry_async(op, model=_model(retry_attempts=2)))


def test_retry_async_does_not_retry_non_transient_errors() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        raise LlmRequestError("HTTP 401")

    with pytest.raises(LlmRequestError, match="HTTP 401"):
        asyncio.run(retry_async(op, model=_model(retry_attempts=5)))

    assert state["calls"] == 1


def test_retry_async_propagates_cancelled_error_immediately() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(retry_async(op, model=_model(retry_attempts=5)))

    assert state["calls"] == 1
