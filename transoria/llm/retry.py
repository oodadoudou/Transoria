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

# Format-drift errors (line count mismatch / duplicates / malformed JSON) almost
# never self-heal across retries — the model produced semantically wrong output,
# not a transport blip. Cap retries at 2 to stop burning tokens on a class of
# failure that ``model.retry_attempts`` (sized for transient 5xx/429/timeout)
# was never meant to cover.
_FORMAT_DRIFT_RETRY_BUDGET = 2


def _is_format_drift_error(exc: BaseException) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return True
    if isinstance(exc, LlmRequestError):
        code = getattr(exc, "code", "")
        if code in ("llm.line_count_mismatch", "llm.duplicate_translations"):
            return True
        if "line count mismatch" in str(exc).lower():
            return True
    return False


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
        code = getattr(exc, "code", "")
        if code in ("llm.transport_error", "llm.duplicate_translations"):
            return True
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

    Transport drift (5xx/429/timeout/transport_error) gets ``model.retry_attempts``
    retries; format drift (line count mismatch / duplicates / JSONDecodeError)
    is capped at ``min(2, model.retry_attempts)`` because more retries rarely
    self-heal a model that already produced semantically wrong output. Backoff
    doubles from ``retry_initial_backoff_seconds`` up to
    ``retry_max_backoff_seconds``. ``asyncio.CancelledError`` is re-raised verbatim.
    """

    transport_remaining = max(0, model.retry_attempts)
    format_remaining = min(_FORMAT_DRIFT_RETRY_BUDGET, transport_remaining)
    backoff = max(0.0, model.retry_initial_backoff_seconds)
    max_backoff = max(backoff, model.retry_max_backoff_seconds)

    while True:
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not should_retry(exc):
                raise
            if _is_format_drift_error(exc):
                if format_remaining <= 0:
                    raise
                format_remaining -= 1
            else:
                if transport_remaining <= 0:
                    raise
                transport_remaining -= 1
            await sleep(min(backoff, max_backoff))
            backoff = min(backoff * 2 if backoff > 0 else 1.0, max_backoff)


__all__ = ["is_transient_llm_error", "retry_async"]
