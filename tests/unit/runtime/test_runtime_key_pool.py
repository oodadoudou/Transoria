"""KeyPool: round-robin acquire, dead-key eviction, all-failed error."""

from __future__ import annotations

import asyncio

import pytest

from transoria.runtime.key_pool import AllKeysFailedError, KeyPool


def test_round_robin_visits_each_key_in_order() -> None:
    pool = KeyPool(("a", "b", "c"))

    sequence = asyncio.run(_drain(pool, 6))

    assert sequence == ["a", "b", "c", "a", "b", "c"]


def test_pool_requires_at_least_one_key() -> None:
    with pytest.raises(ValueError):
        KeyPool(())


def test_mark_dead_removes_key_from_subsequent_rotation() -> None:
    pool = KeyPool(("a", "b", "c"))

    asyncio.run(pool.acquire())  # cursor → 1
    pool.mark_dead("b")
    next_two = asyncio.run(_drain(pool, 2))

    assert "b" not in next_two
    assert pool.alive_count == 2
    assert "b" in pool.dead_keys


def test_acquire_raises_when_every_key_dead() -> None:
    pool = KeyPool(("a", "b"))
    pool.mark_dead("a")
    pool.mark_dead("b")

    with pytest.raises(AllKeysFailedError):
        asyncio.run(pool.acquire())


def test_mark_dead_unknown_key_is_silent() -> None:
    pool = KeyPool(("a",))
    pool.mark_dead("not-in-pool")

    assert pool.alive_count == 1


async def _drain(pool: KeyPool, count: int) -> list[str]:
    result: list[str] = []
    for _ in range(count):
        result.append(await pool.acquire())
    return result
