"""Task-scoped pool of API keys for one model profile.

A profile may carry multiple API keys (e.g. several DeepSeek API keys
under one developer account). When ``rotate_keys`` is on and the user
has more than one key, the runner builds a ``KeyPool`` per task and
threads it through ``ChatRequest``. The LLM client uses the pool to:

- Round-robin across keys on every call (default behavior — spreads
  load to bypass per-key per-minute rate limits).
- Evict a key on persistent auth failure (HTTP 401 / 403); a single
  key's revoked credentials shouldn't sink the whole task.
- Surface a clear ``llm.all_keys_failed`` error when every key has
  been evicted, so the frontend can stop the task and prompt the
  user to refresh credentials.

The pool's state is task-scoped — it is rebuilt fresh on the next
task so a transient outage that revoked all keys doesn't permanently
poison the user's profile.
"""

from __future__ import annotations

import asyncio


class AllKeysFailedError(RuntimeError):
    """Every API key in the pool has been evicted for this task."""


class KeyPool:
    def __init__(self, keys: tuple[str, ...]):
        if not keys:
            raise ValueError("KeyPool requires at least one API key.")
        self._keys: tuple[str, ...] = tuple(keys)
        self._cursor: int = 0
        self._dead: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def total(self) -> int:
        return len(self._keys)

    @property
    def alive_count(self) -> int:
        return self.total - len(self._dead)

    @property
    def dead_keys(self) -> frozenset[str]:
        return frozenset(self._dead)

    async def acquire(self) -> str:
        """Return the next live key in round-robin order.

        Raises :class:`AllKeysFailedError` when every key has been
        evicted. Otherwise advances an internal cursor so consecutive
        calls hit different keys (modulo the alive set).
        """

        async with self._lock:
            alive = [key for key in self._keys if key not in self._dead]
            if not alive:
                raise AllKeysFailedError(
                    f"All {self.total} API keys failed for this task."
                )
            key = alive[self._cursor % len(alive)]
            self._cursor += 1
            return key

    def mark_dead(self, key: str) -> None:
        """Evict ``key`` from rotation for the rest of this task."""

        if key in self._keys:
            self._dead.add(key)


__all__ = ["AllKeysFailedError", "KeyPool"]
