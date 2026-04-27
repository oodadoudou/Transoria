"""Retry helper for transient LLM errors.

Wraps an async callable with exponential backoff. The retry decision is
delegated to a ``should_retry`` callback so each runner can encode its own
policy (e.g. translation retries on line-count mismatches; glossary doesn't).
``asyncio.CancelledError`` is always re-raised verbatim so cooperative
cancellation flows through cleanly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, TypeVar

from transoria.llm.client import LlmRequestError, NoApiKeyError
from transoria.llm.config import ModelConfig


T = TypeVar("T")


def is_transient_llm_error(exc: BaseException) -> bool:
    """Default retryable-error policy.

    Retries:
    - HTTP 5xx responses surfaced as ``LlmRequestError`` with ``HTTP 5xx`` in
      the message
    - ``asyncio.TimeoutError`` and the modern alias ``TimeoutError``
    - JSON decode errors thrown by the response decoder
    - Translation-style line-count mismatches (``LlmRequestError`` containing
      "line count mismatch")

    Does NOT retry:
    - ``NoApiKeyError`` (no point — config issue)
    - ``LlmRequestError`` with HTTP 4xx (auth, bad request)
    - ``asyncio.CancelledError`` (always re-raised)
    """

    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, NoApiKeyError):
        return False
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if isinstance(exc, json.JSONDecodeError):
        return True
    if isinstance(exc, LlmRequestError):
        message = str(exc)
        if "line count mismatch" in message.lower():
            return True
        # HTTP 5xx is retryable; 4xx is not. Match the format produced by
        # ``_chat_openai`` in client.py: ``HTTP <code> from <url>: ...``.
        for code in range(500, 600):
            if f"HTTP {code}" in message:
                return True
        return False
    return False


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    model: ModelConfig,
    should_retry: Callable[[BaseException], bool] = is_transient_llm_error,
    sleep: Callable[[float], "asyncio.Future[None]"] = asyncio.sleep,
) -> T:
    """Run ``operation`` with exponential-backoff retries.

    Total attempts = ``1 + model.retry_attempts``. Backoff starts at
    ``retry_initial_backoff_seconds`` and doubles each round, capped at
    ``retry_max_backoff_seconds``. ``asyncio.CancelledError`` short-circuits
    the loop and is re-raised verbatim.
    """

    attempts_remaining = max(0, model.retry_attempts)
    backoff = max(0.0, model.retry_initial_backoff_seconds)
    max_backoff = max(backoff, model.retry_max_backoff_seconds)

    while True:
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if attempts_remaining <= 0 or not should_retry(exc):
                raise
            attempts_remaining -= 1
            await sleep(min(backoff, max_backoff))
            backoff = min(backoff * 2 if backoff > 0 else 1.0, max_backoff)


__all__ = ["is_transient_llm_error", "retry_async"]
