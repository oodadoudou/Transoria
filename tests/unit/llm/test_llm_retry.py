from __future__ import annotations

import asyncio
import json

import pytest

from transoria.llm import LlmRequestError, NoApiKeyError
from transoria.llm.retry import is_transient_llm_error, retry_async


def test_is_transient_classifies_timeouts_and_5xx_as_retryable() -> None:
    assert is_transient_llm_error(asyncio.TimeoutError())
    assert is_transient_llm_error(TimeoutError())
    assert is_transient_llm_error(LlmRequestError("HTTP 503 from x"))
    assert is_transient_llm_error(LlmRequestError("HTTP 500"))
    assert is_transient_llm_error(
        LlmRequestError(
            "HTTP 500 from x: {'error': {'code': 'ModelLoading'}}"
        )
    )
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


def test_is_transient_retries_transport_error_code() -> None:
    assert is_transient_llm_error(
        LlmRequestError(
            "Transport failed for model 'm': ReadTimeout",
            code="llm.transport_error",
        )
    )


def test_retry_async_succeeds_without_retry_when_first_attempt_works() -> None:
    calls: list[int] = []

    async def op() -> str:
        calls.append(1)
        return "ok"

    result = asyncio.run(retry_async(op, transport_retry_attempts=2))

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
            transport_retry_attempts=3,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=2.0,
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
        asyncio.run(retry_async(op, transport_retry_attempts=2))


def test_retry_async_does_not_retry_non_transient_errors() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        raise LlmRequestError("HTTP 401")

    with pytest.raises(LlmRequestError, match="HTTP 401"):
        asyncio.run(retry_async(op, transport_retry_attempts=5))

    assert state["calls"] == 1


def test_retry_async_caps_format_drift_at_two_retries() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        raise LlmRequestError(
            "Translation line count mismatch — expected [0,1] got [0]",
            code="llm.line_count_mismatch",
        )

    with pytest.raises(LlmRequestError, match="line count mismatch"):
        asyncio.run(retry_async(op, transport_retry_attempts=10))

    # 1 initial + 2 format retries; the generous transport budget is unused.
    assert state["calls"] == 3


def test_retry_async_transport_uses_full_transport_retry_attempts() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        raise LlmRequestError("HTTP 503")

    with pytest.raises(LlmRequestError, match="HTTP 503"):
        asyncio.run(retry_async(op, transport_retry_attempts=4))

    assert state["calls"] == 5


def test_retry_async_can_use_smaller_runtime_budget() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        raise LlmRequestError("HTTP 503")

    with pytest.raises(LlmRequestError, match="HTTP 503"):
        asyncio.run(
            retry_async(
                op,
                transport_retry_attempts=10,
                max_retry_attempts=1,
            )
        )

    assert state["calls"] == 2


def test_retry_async_format_drift_does_not_consume_transport_budget() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        if state["calls"] == 1:
            raise LlmRequestError(
                "line count mismatch",
                code="llm.line_count_mismatch",
            )
        raise LlmRequestError("HTTP 503")

    # call 1 consumes 1 of 2 format budget; calls 2-4 consume 2 of 2 transport
    # budget; call 4 raises with transport_remaining == 0.
    with pytest.raises(LlmRequestError, match="HTTP 503"):
        asyncio.run(retry_async(op, transport_retry_attempts=2))

    assert state["calls"] == 4


def test_retry_async_propagates_cancelled_error_immediately() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(retry_async(op, transport_retry_attempts=5))

    assert state["calls"] == 1
