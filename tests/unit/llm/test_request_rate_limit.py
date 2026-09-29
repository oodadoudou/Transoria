from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from tests.helpers.transport import QueuedTransport
from transoria.llm.client import ChatRequest, LlmClient, LlmRequestError, TransportResult
from transoria.llm.config import ModelConfig, ProviderFormat
from transoria.llm.retry import retry_async
from transoria.runtime import rate_limit
from transoria.workflows.translation.routing import RouteLimitedClient


def _request(**overrides) -> ChatRequest:
    model = ModelConfig(
        id="shared-profile", display_name="Shared", provider_format=ProviderFormat.OPENAI,
        base_url="https://example.test/v1", model_id="model", api_keys=("key",),
        rpm_limit=2, timeout_seconds=1,
    )
    return ChatRequest(model=replace(model, **overrides), system_prompt="", user_prompt="hello")


def _ok() -> TransportResult:
    return TransportResult(200, {"choices": [{"message": {"content": "ok"}}]})


def _virtual_limiter(monkeypatch):
    now = [0.0]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
        await asyncio.sleep(0)

    limiter = rate_limit.SharedRpmLimiter(clock=lambda: now[0], sleep=sleep)
    monkeypatch.setitem(rate_limit._shared_rpm, "shared-profile", limiter)
    return limiter, now, waits


def test_advanced_and_single_clients_share_history_without_double_counting(monkeypatch):
    limiter, now, waits = _virtual_limiter(monkeypatch)
    transport = QueuedTransport([_ok(), _ok(), _ok()])
    routed = RouteLimitedClient(LlmClient(transport))
    single = LlmClient(transport)

    asyncio.run(routed.chat(_request()))
    asyncio.run(single.chat(_request()))
    assert len(limiter._timestamps) == 2
    assert not waits
    asyncio.run(LlmClient(transport).chat(_request()))
    assert now[0] == 60.0
    assert len(transport.captured) == 3


@pytest.mark.parametrize("failure", [
    TransportResult(429, {"error": "rate limited"}),
    TransportResult(400, {"error": {"message": "Unknown parameter: 'thinking'.", "param": "thinking"}}),
])
def test_key_rotation_and_compatibility_retries_each_consume_rpm(monkeypatch, failure):
    _, now, waits = _virtual_limiter(monkeypatch)
    transport = QueuedTransport([failure, _ok()])
    request = _request(api_keys=("first", "second"), rotate_keys=True, rpm_limit=1)

    result = asyncio.run(LlmClient(transport).chat(request))

    assert result.content == "ok"
    assert len(transport.captured) == 2
    assert now[0] == 60.0
    assert waits
    if failure.status_code == 429:
        assert transport.captured[0]["headers"] != transport.captured[1]["headers"]


def test_workflow_retries_obey_the_same_request_limit(monkeypatch):
    _, now, _ = _virtual_limiter(monkeypatch)
    transport = QueuedTransport([TransportResult(503, {}), _ok()])
    client = LlmClient(transport)

    result = asyncio.run(retry_async(
        lambda: client.chat(_request(rpm_limit=1)), transport_retry_attempts=1,
        should_retry=lambda exc: isinstance(exc, LlmRequestError),
    ))

    assert result.content == "ok"
    assert now[0] == 60.0


def test_waiting_for_rpm_does_not_consume_provider_timeout(monkeypatch):
    class SlowAdmission:
        async def acquire(self, _limit):
            await asyncio.sleep(0.03)

    monkeypatch.setattr("transoria.llm.client.shared_rpm_limiter", lambda _: SlowAdmission())
    transport = QueuedTransport([_ok()])
    result = asyncio.run(LlmClient(transport).chat(_request(timeout_seconds=0.01)))
    assert result.content == "ok"


def test_provider_timeout_is_enforced_after_admission():
    class SlowTransport:
        async def execute(self, *_args):
            await asyncio.sleep(0.03)
            return _ok()

    with pytest.raises(LlmRequestError) as caught:
        asyncio.run(LlmClient(SlowTransport()).chat(_request(timeout_seconds=0.01)))
    assert isinstance(caught.value.__cause__, TimeoutError)


def test_stop_cancels_rpm_wait_without_sending_or_leaking_waiter(monkeypatch):
    async def scenario():
        stopped = [False]
        waiting = asyncio.Event()

        async def sleep(_seconds):
            waiting.set()
            await asyncio.sleep(0)

        limiter = rate_limit.SharedRpmLimiter(clock=lambda: 0, sleep=sleep)
        monkeypatch.setitem(rate_limit._shared_rpm, "shared-profile", limiter)
        await limiter.acquire(1)
        transport = QueuedTransport([_ok()])
        with rate_limit.request_stop_scope(lambda: stopped[0]):
            task = asyncio.create_task(LlmClient(transport).chat(_request(rpm_limit=1)))
        await waiting.wait()
        stopped[0] = True
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not transport.captured
        assert not limiter._waiters

    asyncio.run(scenario())
