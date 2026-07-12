"""Task-scoped LLM request logging.

The log intentionally stores provider response text and operational metadata,
but never API keys or full request prompts. Run pages can inspect the exact
model output without leaking credentials into task cache files.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator, Mapping, Protocol
from uuid import uuid4

from transoria.llm.config import ModelConfig
from transoria.llm.usage import TokenUsage

_MAX_TEXT_LENGTH = 20000
_MAX_ERROR_LENGTH = 2000


class RequestEventCache(Protocol):
    def append_request_event(
        self, task_id: str, event: Mapping[str, object]
    ) -> None: ...


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


@dataclass
class RequestLogContext:
    cache: RequestEventCache
    task_id: str
    subtask_id: str
    subtask_attempt: int
    clock: Callable[[], str] = _utc_now_iso
    _request_index: int = field(default=0, init=False, repr=False)

    def next_request_index(self) -> int:
        self._request_index += 1
        return self._request_index


@dataclass(frozen=True)
class RequestLogHandle:
    context: RequestLogContext
    request_id: str
    started_monotonic: float

    def complete(
        self,
        *,
        status_code: int,
        usage: TokenUsage,
        response_text: str,
    ) -> None:
        self._append(
            {
                "status": "completed",
                "phase": "completed",
                "last_activity_at": self.context.clock(),
                "http_status": status_code,
                "duration_seconds": round(time.monotonic() - self.started_monotonic, 3),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "total_tokens": usage.total_tokens,
                "usage_estimated": usage.estimated,
                "response_chars": len(response_text),
                "response_text": _truncate(response_text, _MAX_TEXT_LENGTH),
            }
        )

    def progress(
        self,
        *,
        phase: str,
        status_code: int | None = None,
        response_text: str = "",
        response_chars: int | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "status": "running",
            "phase": phase,
            "last_activity_at": self.context.clock(),
            "duration_seconds": round(time.monotonic() - self.started_monotonic, 3),
        }
        if status_code is not None:
            payload["http_status"] = status_code
        if response_chars is not None:
            payload["response_chars"] = response_chars
        if response_text:
            payload["partial_response_text"] = _truncate(
                response_text, _MAX_TEXT_LENGTH
            )
        self._append(payload)

    def fail(
        self,
        *,
        error: str,
        status_code: int | None = None,
        response_text: str = "",
        response_chars: int | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "status": "failed",
            "phase": "failed",
            "last_activity_at": self.context.clock(),
            "duration_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "error": _truncate(error, _MAX_ERROR_LENGTH),
        }
        if status_code is not None:
            payload["http_status"] = status_code
        if response_chars is not None:
            payload["response_chars"] = response_chars
        if response_text:
            payload["response_text"] = _truncate(response_text, _MAX_TEXT_LENGTH)
        if usage is not None:
            payload.update(usage.to_dict())
        self._append(payload)

    def cancel(self) -> None:
        self._append(
            {
                "status": "cancelled",
                "phase": "cancelled",
                "last_activity_at": self.context.clock(),
                "duration_seconds": round(time.monotonic() - self.started_monotonic, 3),
                "error": "Request was cancelled.",
            }
        )

    def _append(self, patch: Mapping[str, object]) -> None:
        event = {
            **_base_event(self.context, self.request_id),
            **dict(patch),
        }
        _safe_append(self.context.cache, self.context.task_id, event)


_CURRENT_CONTEXT: contextvars.ContextVar[RequestLogContext | None] = (
    contextvars.ContextVar("transoria_request_log_context", default=None)
)


@contextmanager
def request_log_scope(
    cache: RequestEventCache,
    *,
    task_id: str,
    subtask_id: str,
    subtask_attempt: int,
    clock: Callable[[], str] = _utc_now_iso,
) -> Iterator[None]:
    context = RequestLogContext(
        cache=cache,
        task_id=task_id,
        subtask_id=subtask_id,
        subtask_attempt=subtask_attempt,
        clock=clock,
    )
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_CONTEXT.reset(token)


def begin_llm_request(
    *,
    label: str,
    model: ModelConfig,
    provider_attempt: int,
    prompt_chars: int,
) -> RequestLogHandle | None:
    context = _CURRENT_CONTEXT.get()
    if context is None:
        return None
    request_index = context.next_request_index()
    request_id = (
        f"{context.subtask_id}:{context.subtask_attempt}:"
        f"{request_index}:{provider_attempt}:{uuid4().hex[:8]}"
    )
    handle = RequestLogHandle(
        context=context,
        request_id=request_id,
        started_monotonic=time.monotonic(),
    )
    event = {
        **_base_event(context, request_id),
        "status": "running",
        "label": label,
        "model_profile_id": model.id,
        "model_id": model.model_id,
        "provider_format": model.provider_format.value,
        "provider_attempt": provider_attempt,
        "prompt_chars": prompt_chars,
        "timeout_seconds": model.timeout_seconds,
        "phase": "sent",
        "last_activity_at": context.clock(),
    }
    _safe_append(context.cache, context.task_id, event)
    return handle


def append_local_failure(
    *,
    label: str,
    error: str,
    response_text: str = "",
) -> None:
    """Persist a non-provider validation failure in the current request scope."""

    context = _CURRENT_CONTEXT.get()
    if context is None:
        return
    request_index = context.next_request_index()
    request_id = (
        f"{context.subtask_id}:{context.subtask_attempt}:"
        f"local:{request_index}:{uuid4().hex[:8]}"
    )
    event = {
        **_base_event(context, request_id),
        "status": "failed",
        "phase": "validation",
        "last_activity_at": context.clock(),
        "label": label,
        "error": _truncate(error, _MAX_ERROR_LENGTH),
        "response_text": _truncate(response_text or error, _MAX_TEXT_LENGTH),
        "local_failure": True,
    }
    _safe_append(context.cache, context.task_id, event)


def _base_event(
    context: RequestLogContext, request_id: str
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "timestamp": context.clock(),
        "task_id": context.task_id,
        "subtask_id": context.subtask_id,
        "subtask_attempt": context.subtask_attempt,
    }


def _safe_append(
    cache: RequestEventCache, task_id: str, event: Mapping[str, object]
) -> None:
    try:
        cache.append_request_event(task_id, event)
    except Exception:
        # Request logging is diagnostic only. It must never fail the paid LLM
        # request path or change workflow semantics.
        pass


__all__ = ["append_local_failure", "begin_llm_request", "request_log_scope"]
