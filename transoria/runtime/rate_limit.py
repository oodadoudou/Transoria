"""Async rate limiters (sliding window, requests-per-minute and tokens-per-minute)."""

from __future__ import annotations

import asyncio
import time
import threading
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


@dataclass
class SharedRpmLimiter:
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], "asyncio.Future[None]"] = asyncio.sleep
    _timestamps: Deque[Tuple[float, object]] = field(
        default_factory=deque, init=False, repr=False
    )
    _waiters: Deque[object] = field(default_factory=deque, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _evict(self, now: float) -> None:
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0][0] <= cutoff:
            self._timestamps.popleft()

    def try_reserve(self, limit: int) -> object | None:
        ticket = object()
        if limit <= 0:
            return ticket
        with self._lock:
            now = self.clock()
            self._evict(now)
            if self._waiters or len(self._timestamps) >= limit:
                return None
            self._timestamps.append((now, ticket))
        return ticket

    def release(self, ticket: object) -> None:
        with self._lock:
            for entry in self._timestamps:
                if entry[1] is ticket:
                    self._timestamps.remove(entry)
                    return

    def claim(self, ticket: object) -> bool:
        with self._lock:
            now = self.clock()
            self._evict(now)
            for entry in self._timestamps:
                if entry[1] is ticket:
                    self._timestamps.remove(entry)
                    self._timestamps.append((now, ticket))
                    return True
        return False

    def available_after(self, limit: int) -> float:
        if limit <= 0:
            return 0.0
        with self._lock:
            now = self.clock()
            self._evict(now)
            if self._waiters and len(self._timestamps) < limit:
                return 0.1
            if len(self._timestamps) < limit:
                return 0.0
            return max(0.01, 60.0 - (now - self._timestamps[0][0]))

    async def acquire(self, limit: int) -> None:
        if limit <= 0:
            return
        ticket = object()
        with self._lock:
            self._waiters.append(ticket)
        try:
            while True:
                with self._lock:
                    now = self.clock()
                    self._evict(now)
                    if self._waiters[0] is ticket and len(self._timestamps) < limit:
                        self._waiters.popleft()
                        self._timestamps.append((now, ticket))
                        return
                    wait_for = (
                        60.0 - (now - self._timestamps[0][0])
                        if len(self._timestamps) >= limit
                        else 0.1
                    )
                await self.sleep(min(max(wait_for, 0.01), 1.0))
        finally:
            with self._lock:
                if ticket in self._waiters:
                    self._waiters.remove(ticket)


_shared_rpm_lock = threading.Lock()
_shared_rpm: dict[str, SharedRpmLimiter] = {}


def shared_rpm_limiter(profile_id: str) -> SharedRpmLimiter:
    with _shared_rpm_lock:
        return _shared_rpm.setdefault(profile_id, SharedRpmLimiter())


__all__ = ["RpmLimiter", "TpmLimiter", "SharedRpmLimiter", "shared_rpm_limiter"]
