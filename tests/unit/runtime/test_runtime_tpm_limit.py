from __future__ import annotations

import asyncio

from transoria.runtime.rate_limit import TpmLimiter


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_tpm_admits_requests_under_budget() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    clock = _FakeClock()
    limiter = TpmLimiter(limit=1000, window=60.0, clock=clock, sleep=fake_sleep)

    async def scenario() -> None:
        r1 = await limiter.reserve(400)
        r2 = await limiter.reserve(400)
        assert r1 > 0 and r2 > 0

    asyncio.run(scenario())
    assert sleeps == []
    assert limiter.used_in_window() == 800


def test_tpm_blocks_then_admits_after_window_evicts() -> None:
    clock = _FakeClock()

    async def fake_sleep(seconds: float) -> None:
        clock.advance(seconds)

    limiter = TpmLimiter(limit=500, window=60.0, clock=clock, sleep=fake_sleep)

    async def scenario() -> None:
        await limiter.reserve(300)
        await limiter.reserve(300)  # forces sleep until first reservation evicts

    asyncio.run(scenario())
    # Eviction window must have elapsed.
    assert clock.now >= 60.0


def test_tpm_settle_replaces_estimate_with_actual() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = TpmLimiter(limit=1000, window=60.0, sleep=fake_sleep)

    async def scenario() -> None:
        rid = await limiter.reserve(800)
        assert limiter.used_in_window() == 800
        limiter.settle(rid, actual_tokens=200)
        assert limiter.used_in_window() == 200

    asyncio.run(scenario())


def test_tpm_zero_limit_is_a_noop() -> None:
    limiter = TpmLimiter(limit=0)

    async def scenario() -> None:
        rid = await limiter.reserve(10_000_000)
        assert rid == -1
        # Settle on a no-op reservation is harmless.
        limiter.settle(rid, 1)

    asyncio.run(scenario())
    assert limiter.used_in_window() == 0
