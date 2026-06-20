from __future__ import annotations

import asyncio

from transoria.runtime import RpmLimiter


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_rpm_limiter_admits_under_limit_without_sleep() -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    clock = _FakeClock()
    limiter = RpmLimiter(limit=3, window=60.0, clock=clock, sleep=fake_sleep)

    async def scenario() -> None:
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()

    asyncio.run(scenario())

    assert sleep_calls == []
    assert limiter.in_flight_count() == 3


def test_rpm_limiter_blocks_when_window_full_then_releases_after_eviction() -> None:
    sleep_calls: list[float] = []
    clock = _FakeClock()

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.advance(seconds)

    limiter = RpmLimiter(limit=2, window=60.0, clock=clock, sleep=fake_sleep)

    async def scenario() -> None:
        await limiter.acquire()  # t=0
        await limiter.acquire()  # t=0
        # The next acquire fills the window; should sleep ~60s.
        await limiter.acquire()

    asyncio.run(scenario())

    assert sleep_calls and sleep_calls[0] > 0
    # After eviction, only the most recent two timestamps remain.
    assert limiter.in_flight_count() <= 2


def test_rpm_limiter_with_zero_limit_is_a_noop() -> None:
    limiter = RpmLimiter(limit=0)

    asyncio.run(limiter.acquire())
    asyncio.run(limiter.acquire())

    assert limiter.in_flight_count() == 0
