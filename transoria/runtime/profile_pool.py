"""Round-robin dispatcher across multiple model profiles.

A task starts with an ordered list of ``ModelConfig``. Each LLM call
the runner makes goes through ``ProfilePool.acquire()`` which picks the
next profile in rotation, gated by that profile's per-minute request
bucket.

Goals:

- **Bypass per-minute rate limits**: with N profiles configured, the
  effective ceiling is the sum of their ``rpm_limit`` values.
- **Preserve top-to-bottom ordering** under no pressure: with N=2 and
  no rate-limit pressure, calls go A, B, A, B, …
- **Avoid blocking on a saturated profile** when another has slack:
  the dispatcher prefers profiles with current bucket capacity over
  the round-robin pick if the round-robin pick is saturated.
- **Task-scoped failure exclusion**: if a profile's keys all fail
  (HTTP 401/403 across rotation), the runner can flag it via
  ``mark_failed`` and the pool drops it for the rest of this task.
  Not persisted; a fresh task starts with the full pool.

Per-profile TPM buckets are managed here too so the runner can
``reserve``/``settle`` per call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from transoria.llm.config import ModelConfig
from transoria.runtime.rate_limit import RpmLimiter, TpmLimiter


class AllProfilesExhaustedError(RuntimeError):
    """All profiles in the pool have been excluded from rotation."""


@dataclass
class _PoolEntry:
    profile: ModelConfig
    rpm_limiter: RpmLimiter
    tpm_limiter: TpmLimiter | None


class ProfilePool:
    def __init__(self, profiles: tuple[ModelConfig, ...]):
        if not profiles:
            raise ValueError("ProfilePool requires at least one profile.")
        self._entries: list[_PoolEntry] = [
            _PoolEntry(
                profile=profile,
                rpm_limiter=RpmLimiter(limit=profile.rpm_limit),
                tpm_limiter=(
                    TpmLimiter(limit=profile.tpm_limit)
                    if profile.tpm_limit > 0
                    else None
                ),
            )
            for profile in profiles
        ]
        self._index_by_id: dict[str, int] = {
            entry.profile.id: index
            for index, entry in enumerate(self._entries)
        }
        self._cursor: int = 0
        self._excluded: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def primary(self) -> ModelConfig:
        """First profile of the original list. Used as the canonical
        source for retry policy and prompt-build thinking flag."""

        return self._entries[0].profile

    @property
    def all_profiles(self) -> tuple[ModelConfig, ...]:
        return tuple(entry.profile for entry in self._entries)

    @property
    def excluded_ids(self) -> frozenset[str]:
        return frozenset(self._excluded)

    async def acquire(self) -> ModelConfig:
        async with self._lock:
            n = len(self._entries)
            eligible = [
                index
                for index in range(n)
                if self._entries[index].profile.id not in self._excluded
            ]
            if not eligible:
                raise AllProfilesExhaustedError(
                    "All configured model profiles failed for this task."
                )
            chosen = self._pick_with_capacity(eligible, n)
            if chosen is None:
                chosen = self._pick_round_robin(eligible, n)
            self._cursor = (chosen + 1) % n
            entry = self._entries[chosen]
        await entry.rpm_limiter.acquire()
        return entry.profile

    def _pick_with_capacity(self, eligible: list[int], n: int) -> int | None:
        for offset in range(n):
            index = (self._cursor + offset) % n
            if index not in eligible:
                continue
            entry = self._entries[index]
            if entry.profile.rpm_limit <= 0:
                return index
            if entry.rpm_limiter.in_flight_count() < entry.profile.rpm_limit:
                return index
        return None

    def _pick_round_robin(self, eligible: list[int], n: int) -> int:
        for offset in range(n):
            index = (self._cursor + offset) % n
            if index in eligible:
                return index
        return eligible[0]

    def tpm_for(self, profile_id: str) -> TpmLimiter | None:
        index = self._index_by_id.get(profile_id)
        if index is None:
            return None
        return self._entries[index].tpm_limiter

    def mark_failed(self, profile_id: str) -> None:
        if profile_id in self._index_by_id:
            self._excluded.add(profile_id)


__all__ = ["AllProfilesExhaustedError", "ProfilePool"]
