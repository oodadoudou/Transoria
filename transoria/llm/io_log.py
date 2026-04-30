"""Stderr logger for LLM request/response IO.

Goal: give the human running the app a real-time pulse on what each
chunk is sending and receiving, without bolting a UI panel onto the
frontend. Output goes to stderr (the terminal that launched the app),
not stdout, so it doesn't pollute pipelines.

Verbosity is controlled by ``TRANSORIA_LLM_LOG``:

- ``off``      — no IO log
- ``compact``  — one short line per send + one per recv (default)
- ``full``     — compact lines plus the full request user-prompt and
                 response content (very noisy at concurrency > 4)

Compact lines look like:

    [12:34:56.789] [glossary chunk-00012] SEND model=deepseek-v4-pro prompt=2.4KB
    [12:34:58.012] [glossary chunk-00012] RECV 1.2s OK tokens=856/342 reply=1.1KB

The label is supplied by the runner (kind + chunk-id). Empty label
falls back to the model id so untagged calls (e.g. probe / test) still
log a coherent line.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from typing import Mapping


_VERBOSITY_ENV = "TRANSORIA_LLM_LOG"
_VERBOSITY_OFF = "off"
_VERBOSITY_COMPACT = "compact"
_VERBOSITY_FULL = "full"
# Defaults to off because pywebview macOS apps often run without a
# terminal-attached stderr; cross-thread writes there have crashed
# Python. Users opt in by exporting ``TRANSORIA_LLM_LOG=compact`` (or
# ``full``) before launching the backend / dev shell.
_VERBOSITY_DEFAULT = _VERBOSITY_OFF
_VERBOSITY_VALID = {_VERBOSITY_OFF, _VERBOSITY_COMPACT, _VERBOSITY_FULL}

# stderr writes are not atomic across threads — guard so concurrent
# subtasks don't shred each other's lines.
_LOCK = threading.Lock()


def _verbosity() -> str:
    raw = os.environ.get(_VERBOSITY_ENV, _VERBOSITY_DEFAULT).strip().lower()
    return raw if raw in _VERBOSITY_VALID else _VERBOSITY_DEFAULT


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.2f}MB"


def _payload_size(payload: Mapping[str, object]) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False))
    except (TypeError, ValueError):
        return -1


def _emit(line: str) -> None:
    """Write ``line`` to stderr if reachable. Swallows every failure —
    a debug log must never bring the host down. pywebview .app bundles
    on macOS routinely run with ``sys.stderr`` set to a closed fd or
    detached from any terminal; writing there can raise OSError or
    even crash the interpreter under some configurations."""

    stream = sys.stderr
    if stream is None:
        return
    try:
        with _LOCK:
            stream.write(line + "\n")
            try:
                stream.flush()
            except (OSError, ValueError, AttributeError):
                pass
    except (OSError, ValueError, AttributeError):
        return


def _label_or_model(label: str, model_id: str) -> str:
    return label or f"model-{model_id}"


def _extract_user_prompt(payload: Mapping[str, object]) -> str:
    """Best-effort grab of the last user message — used by full mode."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for entry in reversed(messages):
        if not isinstance(entry, dict):
            continue
        if entry.get("role") == "user":
            content = entry.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Anthropic content blocks
                texts = [
                    str(blk.get("text", ""))
                    for blk in content
                    if isinstance(blk, dict) and blk.get("type") == "text"
                ]
                return "\n".join(t for t in texts if t)
    return ""


def _extract_response_text(body: Mapping[str, object]) -> str:
    """OpenAI-shape parsing — Anthropic / Gemini bodies look different
    but the runner only needs a rough preview here, so we return the
    OpenAI ``content`` when present and JSON-dump otherwise."""
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
    try:
        return json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(body)


def log_send(label: str, model_id: str, payload: Mapping[str, object]) -> None:
    verbosity = _verbosity()
    if verbosity == _VERBOSITY_OFF:
        return
    size = _payload_size(payload)
    size_str = _human_bytes(size) if size >= 0 else "?"
    head = f"[{_ts()}] [{_label_or_model(label, model_id)}] SEND model={model_id} prompt={size_str}"
    if verbosity == _VERBOSITY_FULL:
        prompt = _extract_user_prompt(payload)
        _emit(head + "\n--- prompt ---\n" + prompt + "\n---")
    else:
        _emit(head)


def log_recv(
    label: str,
    model_id: str,
    *,
    latency_seconds: float,
    status_code: int,
    body: Mapping[str, object],
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    verbosity = _verbosity()
    if verbosity == _VERBOSITY_OFF:
        return
    text = _extract_response_text(body)
    size = len(text.encode("utf-8")) if isinstance(text, str) else 0
    size_str = _human_bytes(size)
    state = "OK" if status_code < 400 else f"HTTP{status_code}"
    head = (
        f"[{_ts()}] [{_label_or_model(label, model_id)}] RECV "
        f"{latency_seconds:.2f}s {state} tokens={input_tokens}/{output_tokens} reply={size_str}"
    )
    if verbosity == _VERBOSITY_FULL:
        _emit(head + "\n--- reply ---\n" + text + "\n---")
    else:
        _emit(head)


def log_error(
    label: str,
    model_id: str,
    *,
    latency_seconds: float,
    error: BaseException,
) -> None:
    if _verbosity() == _VERBOSITY_OFF:
        return
    _emit(
        f"[{_ts()}] [{_label_or_model(label, model_id)}] FAIL "
        f"{latency_seconds:.2f}s {type(error).__name__}: {error}"
    )


__all__ = ["log_send", "log_recv", "log_error"]
