"""Async rate limiters (sliding window, requests-per-minute and tokens-per-minute)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Tuple


@dataclass
class RpmLimiter:
    """Sliding-window requests-per-minute limiter.

    ``acquire()`` returns immediately when fewer than ``limit`` requests have
    been logged in the past ``window`` seconds. Otherwise it sleeps until the
    oldest logged request ages out of the window, then re-checks. The clock
    function is injectable so tests can run deterministically without real
    sleeps.
    """

    limit: int
    window: float = 60.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], "asyncio.Future[None]"] = asyncio.sleep
    _timestamps: Deque[float] = field(default_factory=deque, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def acquire(self) -> None:
        if self.limit <= 0:
            return
        async with self._lock:
            while True:
                now = self.clock()
                self._evict(now)
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return
                wait_for = self.window - (now - self._timestamps[0])
                if wait_for <= 0:
                    continue
                await self.sleep(wait_for)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def in_flight_count(self) -> int:
        """Logged-but-not-yet-evicted requests, for observability/tests."""

        self._evict(self.clock())
        return len(self._timestamps)


@dataclass
class TpmLimiter:
    """Tokens-per-minute sliding-window limiter.

    Two-phase usage so the runner can settle on a final cost:

    1. ``await limiter.reserve(estimated)`` blocks until ``estimated`` tokens
       fit in the remaining window budget, then logs the estimate as the
       reservation.
    2. ``limiter.settle(reservation, actual)`` replaces the estimate with the
       actual usage reported by the provider's ``usage`` block. Refunds
       overestimates so the next caller doesn't wait unnecessarily.

    A reservation that is never settled simply ages out of the window.
    """

    limit: int
    window: float = 60.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], "asyncio.Future[None]"] = asyncio.sleep
    _entries: Deque[Tuple[float, int, int]] = field(
        default_factory=deque, init=False, repr=False
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _next_id: int = field(default=0, init=False, repr=False)

    async def reserve(self, estimated_tokens: int) -> int:
        if self.limit <= 0 or estimated_tokens <= 0:
            return -1
        async with self._lock:
            while True:
                now = self.clock()
                self._evict(now)
                used = sum(tokens for _, _, tokens in self._entries)
                if used + estimated_tokens <= self.limit:
                    self._next_id += 1
                    reservation_id = self._next_id
                    self._entries.append((now, reservation_id, estimated_tokens))
                    return reservation_id
                wait_for = self.window - (now - self._entries[0][0])
                if wait_for <= 0:
                    continue
                await self.sleep(wait_for)

    def settle(self, reservation_id: int, actual_tokens: int) -> None:
        if reservation_id < 0 or self.limit <= 0:
            return
        for index, (timestamp, rid, _tokens) in enumerate(self._entries):
            if rid == reservation_id:
                self._entries[index] = (timestamp, rid, max(0, actual_tokens))
                return

    def _evict(self, now: float) -> None:
        cutoff = now - self.window
        while self._entries and self._entries[0][0] <= cutoff:
            self._entries.popleft()

    def used_in_window(self) -> int:
        self._evict(self.clock())
        return sum(tokens for _, _, tokens in self._entries)


__all__ = ["RpmLimiter", "TpmLimiter"]
