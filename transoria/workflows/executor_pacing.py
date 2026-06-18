"""Shared executor pacing knobs for LLM workflows."""

from __future__ import annotations


_HIGH_CONCURRENCY_LAUNCH_SPACING_THRESHOLD = 8
_HIGH_CONCURRENCY_LAUNCH_SPACING_SECONDS = 0.05


def llm_launch_spacing_seconds(concurrency_limit: int) -> float:
    if concurrency_limit >= _HIGH_CONCURRENCY_LAUNCH_SPACING_THRESHOLD:
        return _HIGH_CONCURRENCY_LAUNCH_SPACING_SECONDS
    return 0.0


__all__ = ["llm_launch_spacing_seconds"]
