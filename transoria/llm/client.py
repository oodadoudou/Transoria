"""OpenAI-compatible chat client with API-key rotation and pluggable transport.

The client is async-first because the task runtime in Section 10 needs
asyncio-friendly concurrency limits and cooperative cancellation. Tests inject
a fake :class:`ChatTransport`; production uses :class:`HttpxChatTransport`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Mapping, Protocol

import httpx

from transoria.llm.config import ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.io_log import log_error, log_recv, log_send
from transoria.llm.usage import TokenUsage
from transoria.runtime.key_pool import AllKeysFailedError, KeyPool


class LlmRequestError(RuntimeError):
    """Raised when the provider returns a non-recoverable error.

    Carries a stable ``code`` string for frontend localization. The default
    is ``llm.error``; specific call sites set codes like
    ``llm.http_error``, ``llm.line_count_mismatch``, ``llm.transport_error``,
    or ``llm.no_api_key`` (subclass) so the UI can render localized messages
    without parsing the human-readable text.
    """

    code: str = "llm.error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class NoApiKeyError(LlmRequestError):
    """Raised when the model has no resolved API keys."""

    code: str = "llm.no_api_key"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def to_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatRequest:
    model: ModelConfig
    system_prompt: str
    user_prompt: str
    history: tuple[ChatMessage, ...] = field(default_factory=tuple)
    temperature: float | None = None
    stream: bool = False
    # Optional task-scoped key pool. When provided, the client picks
    # keys via round-robin from the pool instead of iterating
    # ``model.api_keys`` in order. Persistent auth failures evict
    # the offending key; pool exhaustion raises
    # ``llm.all_keys_failed``.
    key_pool: KeyPool | None = None
    # Free-form tag the runner sets to identify this request in stderr
    # IO logs, e.g. ``"glossary chunk-00012"``.
    log_label: str = ""


@dataclass(frozen=True)
class ChatResponse:
    content: str
    usage: TokenUsage
    raw: Mapping[str, object] | None = None


@dataclass(frozen=True)
class TransportResult:
    status_code: int
    body: Mapping[str, object]


class ChatTransport(Protocol):
    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult: ...


class HttpxChatTransport:
    """Default transport using ``httpx.AsyncClient``.

    When the request payload sets ``stream=True`` the transport opens a
    streaming connection and accumulates Server-Sent-Events ``data:`` lines
    into the synthesized response body. This keeps the public return type
    stable (``TransportResult``) while letting the caller cancel mid-stream
    via ``asyncio.CancelledError`` — the underlying connection closes
    immediately rather than waiting for the full response to drain.
    """

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        if bool(payload.get("stream")):
            return await self._execute_streaming(url, headers, payload, timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=dict(headers), json=dict(payload))
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        return TransportResult(status_code=response.status_code, body=body)

    async def _execute_streaming(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        import json

        chunks: list[str] = []
        usage: Mapping[str, object] | None = None
        status_code = 0
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", url, headers=dict(headers), json=dict(payload)
            ) as response:
                status_code = response.status_code
                if status_code >= 400:
                    body_text = await response.aread()
                    return TransportResult(
                        status_code=status_code,
                        body={"raw_text": body_text.decode("utf-8", "replace")},
                    )
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith(":") or line.startswith("event:"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    # OpenAI / Volcengine: ``choices[*].delta.content``
                    choices = event.get("choices") or []
                    for choice in choices:
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if isinstance(text, str):
                            chunks.append(text)
                    # Anthropic: ``content_block_delta`` carries
                    # ``delta.text``; ``message_delta`` carries ``usage``;
                    # ``message_start`` carries an initial ``usage`` block.
                    event_type = event.get("type")
                    if event_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        if isinstance(delta, Mapping):
                            text = delta.get("text")
                            if isinstance(text, str):
                                chunks.append(text)
                    if event_type in ("message_start", "message_delta"):
                        message_block = event.get("message") or {}
                        message_usage = (
                            message_block.get("usage") if isinstance(message_block, Mapping) else None
                        )
                        if isinstance(message_usage, Mapping):
                            usage = {**(usage or {}), **dict(message_usage)}
                        delta_usage = event.get("usage")
                        if isinstance(delta_usage, Mapping):
                            usage = {**(usage or {}), **dict(delta_usage)}
                    if "usage" in event and isinstance(event["usage"], Mapping):
                        usage = {**(usage or {}), **dict(event["usage"])}

        accumulated = "".join(chunks)
        # Synthesize a body that satisfies all three provider parsers — the
        # streaming transport doesn't know which provider it served, but the
        # subsequent parser does, and each looks at a different field.
        body: dict[str, object] = {
            # OpenAI / Sakura / Custom
            "choices": [
                {"message": {"role": "assistant", "content": accumulated}}
            ],
            # Anthropic
            "content": [{"type": "text", "text": accumulated}],
            # Google generateContent
            "candidates": [
                {"content": {"parts": [{"text": accumulated}]}}
            ],
        }
        if usage is not None:
            body["usage"] = dict(usage)
            # Mirror to Google's usageMetadata for parser symmetry.
            usage_meta: dict[str, object] = {}
            if "input_tokens" in usage:
                usage_meta["promptTokenCount"] = usage["input_tokens"]
            if "output_tokens" in usage:
                usage_meta["candidatesTokenCount"] = usage["output_tokens"]
            if "prompt_tokens" in usage:
                usage_meta.setdefault("promptTokenCount", usage["prompt_tokens"])
            if "completion_tokens" in usage:
                usage_meta.setdefault(
                    "candidatesTokenCount", usage["completion_tokens"]
                )
            if usage_meta:
                body["usageMetadata"] = usage_meta
        return TransportResult(status_code=status_code, body=body)


# HTTP statuses that warrant rotating to the next API key.
_ROTATABLE_STATUSES: frozenset[int] = frozenset({401, 403, 429})

# Thinking-level keyword sent to OpenAI-compatible reasoning models.
# Volcengine Ark and DeepSeek hybrid models accept ``thinking.type=enabled``;
# the ``effort`` field mirrors OpenAI's ``reasoning_effort`` for providers that
# expose graded reasoning. Providers ignore unknown sub-fields in practice.
def _thinking_payload(level: ThinkingLevel) -> dict[str, object] | None:
    if level is ThinkingLevel.OFF:
        return None
    return {"type": "enabled", "effort": level.value}


# Anthropic's ``/v1/messages`` requires a non-zero ``max_tokens``; sending
# 0 is rejected outright. Mirror the convention used by other tooling: a
# user value of 0 / negative is treated as "auto" and replaced with this
# minimum so the API accepts the request and the model has enough headroom
# for typical translation responses.
_ANTHROPIC_AUTO_MAX_TOKENS: int = 8192


def _anthropic_max_tokens(configured: int) -> int:
    """Convert a possibly-zero ``max_output_tokens`` into a value
    Anthropic will accept. Zero or negative means the user wants the
    provider default; we substitute a safe minimum because Anthropic
    has no provider default for this field."""
    if configured > 0:
        return configured
    return _ANTHROPIC_AUTO_MAX_TOKENS


@dataclass(frozen=True)
class LlmClient:
    transport: ChatTransport

    async def chat(self, request: ChatRequest) -> ChatResponse:
        provider = request.model.provider_format
        if provider in (
            ProviderFormat.OPENAI,
            ProviderFormat.SAKURA,
            ProviderFormat.CUSTOM,
        ):
            return await self._chat_openai(request)
        if provider is ProviderFormat.ANTHROPIC:
            return await self._chat_anthropic(request)
        if provider is ProviderFormat.GOOGLE:
            return await self._chat_google(request)
        raise LlmRequestError(
            f"Unsupported provider format: {provider.value}",
            code="llm.unsupported_provider",
        )

    async def _chat_openai(self, request: ChatRequest) -> ChatResponse:
        if not request.model.api_keys:
            raise NoApiKeyError(
                f"No API keys configured for model {request.model.id!r}"
            )

        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(message.to_payload() for message in request.history)
        messages.append({"role": "user", "content": request.user_prompt})

        payload: dict[str, object] = {
            "model": request.model.model_id,
            "messages": messages,
        }
        # Per-call temperature override (request scope) wins; otherwise the
        # model-level override applies; otherwise we omit the key entirely.
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        elif request.model.temperature is not None:
            payload["temperature"] = request.model.temperature
        if request.model.top_p is not None:
            payload["top_p"] = request.model.top_p
        if request.model.presence_penalty is not None:
            payload["presence_penalty"] = request.model.presence_penalty
        if request.model.frequency_penalty is not None:
            payload["frequency_penalty"] = request.model.frequency_penalty
        if request.stream:
            payload["stream"] = True
        thinking = _thinking_payload(request.model.thinking_level)
        if thinking is not None:
            payload["thinking"] = thinking

        url = request.model.base_url.rstrip("/") + "/chat/completions"
        custom = request.model.custom_headers_dict()
        return await self._send_with_rotation(
            request,
            url,
            payload,
            header_factory=lambda key: {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                **custom,
            },
            parser=_parse_openai_response,
        )

    async def _chat_anthropic(self, request: ChatRequest) -> ChatResponse:
        if not request.model.api_keys:
            raise NoApiKeyError(
                f"No API keys configured for model {request.model.id!r}"
            )

        messages: list[dict[str, str]] = []
        messages.extend(message.to_payload() for message in request.history)
        messages.append({"role": "user", "content": request.user_prompt})

        payload: dict[str, object] = {
            "model": request.model.model_id,
            "messages": messages,
            "max_tokens": _anthropic_max_tokens(request.model.max_output_tokens),
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        elif request.model.temperature is not None:
            payload["temperature"] = request.model.temperature
        if request.model.top_p is not None:
            payload["top_p"] = request.model.top_p
        if request.model.thinking_enabled:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": request.model.thinking_budget_tokens,
            }

        url = request.model.base_url.rstrip("/") + "/v1/messages"
        custom = request.model.custom_headers_dict()
        return await self._send_with_rotation(
            request,
            url,
            payload,
            header_factory=lambda key: {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                **custom,
            },
            parser=_parse_anthropic_response,
        )

    async def _chat_google(self, request: ChatRequest) -> ChatResponse:
        if not request.model.api_keys:
            raise NoApiKeyError(
                f"No API keys configured for model {request.model.id!r}"
            )

        contents: list[dict[str, object]] = []
        contents.extend(
            {
                "role": message.role,
                "parts": [{"text": message.content}],
            }
            for message in request.history
        )
        contents.append({"role": "user", "parts": [{"text": request.user_prompt}]})

        payload: dict[str, object] = {"contents": contents}
        if request.system_prompt:
            payload["systemInstruction"] = {
                "role": "system",
                "parts": [{"text": request.system_prompt}],
            }
        generation_config: dict[str, object] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.model.thinking_enabled:
            generation_config["thinkingConfig"] = {
                "thinkingBudget": request.model.thinking_budget_tokens
            }
        if generation_config:
            payload["generationConfig"] = generation_config

        # Google uses URL-bound API keys: ?key=<key>
        url_template = (
            request.model.base_url.rstrip("/")
            + f"/v1beta/models/{request.model.model_id}:generateContent?key={{key}}"
        )
        return await self._send_with_rotation(
            request,
            url_template,
            payload,
            header_factory=lambda _key: {"Content-Type": "application/json"},
            parser=_parse_google_response,
            url_takes_key=True,
        )

    async def _send_with_rotation(
        self,
        request: ChatRequest,
        url_or_template: str,
        payload: dict[str, object],
        *,
        header_factory,
        parser,
        url_takes_key: bool = False,
    ) -> ChatResponse:
        if request.key_pool is not None:
            return await self._send_with_pool(
                request,
                url_or_template,
                payload,
                header_factory=header_factory,
                parser=parser,
                url_takes_key=url_takes_key,
            )

        keys = list(request.model.api_keys)
        attempts = len(keys) if request.model.rotate_keys else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            key = keys[attempt % len(keys)]
            url = url_or_template.format(key=key) if url_takes_key else url_or_template
            headers = header_factory(key)
            log_send(request.log_label, request.model.id, payload)
            send_start = time.monotonic()
            try:
                result = await self.transport.execute(
                    url, headers, payload, request.model.timeout_seconds
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_error(
                    request.log_label,
                    request.model.id,
                    latency_seconds=time.monotonic() - send_start,
                    error=exc,
                )
                last_error = exc
                if attempt + 1 < attempts:
                    continue
                raise LlmRequestError(
                    f"Transport failed for model {request.model.id!r}: {exc}",
                    code="llm.transport_error",
                ) from exc

            latency = time.monotonic() - send_start
            response = parser(result.body) if result.status_code < 400 else None
            log_recv(
                request.log_label,
                request.model.id,
                latency_seconds=latency,
                status_code=result.status_code,
                body=result.body,
                input_tokens=response.usage.input_tokens if response else 0,
                output_tokens=response.usage.output_tokens if response else 0,
            )

            if (
                result.status_code in _ROTATABLE_STATUSES
                and request.model.rotate_keys
                and attempt + 1 < attempts
            ):
                last_error = LlmRequestError(
                    f"HTTP {result.status_code}: {result.body!r}"
                )
                continue

            if result.status_code >= 400:
                raise LlmRequestError(
                    f"HTTP {result.status_code} from {url}: {result.body!r}",
                    code="llm.http_error",
                )

            return response  # type: ignore[return-value]

        raise LlmRequestError(
            f"All API keys failed for model {request.model.id!r}: {last_error}",
            code="llm.all_keys_failed",
        )

    async def _send_with_pool(
        self,
        request: ChatRequest,
        url_or_template: str,
        payload: dict[str, object],
        *,
        header_factory,
        parser,
        url_takes_key: bool,
    ) -> ChatResponse:
        """Round-robin path. The pool picks the next key per call;
        HTTP 401/403 evicts the key permanently for this task; HTTP 429
        is treated as transient (retry without eviction); transport
        failures don't evict (could be network blip)."""

        assert request.key_pool is not None
        pool = request.key_pool
        last_error: Exception | None = None

        while True:
            try:
                key = await pool.acquire()
            except AllKeysFailedError as exc:
                detail = f": {last_error}" if last_error is not None else ""
                raise LlmRequestError(
                    f"All API keys failed for model {request.model.id!r}{detail}",
                    code="llm.all_keys_failed",
                ) from exc

            url = url_or_template.format(key=key) if url_takes_key else url_or_template
            headers = header_factory(key)

            log_send(request.log_label, request.model.id, payload)
            send_start = time.monotonic()
            try:
                result = await self.transport.execute(
                    url, headers, payload, request.model.timeout_seconds
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_error(
                    request.log_label,
                    request.model.id,
                    latency_seconds=time.monotonic() - send_start,
                    error=exc,
                )
                # Transport / network error — don't evict; surface to
                # the runner so retry_async can decide whether to retry.
                raise LlmRequestError(
                    f"Transport failed for model {request.model.id!r}: {exc}",
                    code="llm.transport_error",
                ) from exc

            latency = time.monotonic() - send_start
            response = parser(result.body) if result.status_code < 400 else None
            log_recv(
                request.log_label,
                request.model.id,
                latency_seconds=latency,
                status_code=result.status_code,
                body=result.body,
                input_tokens=response.usage.input_tokens if response else 0,
                output_tokens=response.usage.output_tokens if response else 0,
            )

            if result.status_code in {401, 403}:
                pool.mark_dead(key)
                last_error = LlmRequestError(
                    f"HTTP {result.status_code} (auth failure) from {url}: {result.body!r}"
                )
                continue

            if result.status_code == 429:
                # Per-key rate limit — try the next key without evicting.
                last_error = LlmRequestError(
                    f"HTTP 429 (rate limited) from {url}: {result.body!r}"
                )
                continue

            if result.status_code >= 400:
                raise LlmRequestError(
                    f"HTTP {result.status_code} from {url}: {result.body!r}",
                    code="llm.http_error",
                )

            return response  # type: ignore[return-value]


def _parse_openai_response(body: Mapping[str, object]) -> ChatResponse:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmRequestError(
        f"No choices in response: {body!r}",
        code="llm.malformed_response",
    )
    first = choices[0]
    if not isinstance(first, Mapping):
        raise LlmRequestError(
        f"Malformed choice: {first!r}",
        code="llm.malformed_response",
    )
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise LlmRequestError(
        f"Malformed message: {first!r}",
        code="llm.malformed_response",
    )
    content = message.get("content", "")
    usage_block = body.get("usage")
    usage = (
        TokenUsage.from_openai_usage(usage_block)
        if isinstance(usage_block, Mapping)
        else TokenUsage()
    )
    return ChatResponse(content=str(content), usage=usage, raw=body)


def _parse_anthropic_response(body: Mapping[str, object]) -> ChatResponse:
    """Anthropic ``/v1/messages`` returns ``content`` as a list of blocks.

    Each block has a ``type`` (``text`` for normal output) and a ``text``
    field. We concatenate all text blocks. Token usage comes from
    ``usage.input_tokens`` / ``usage.output_tokens``.
    """

    blocks = body.get("content")
    if not isinstance(blocks, list):
        raise LlmRequestError(
        f"Anthropic response missing content: {body!r}",
        code="llm.malformed_response",
    )
    text_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
    usage_block = body.get("usage")
    usage = (
        TokenUsage.from_openai_usage(usage_block)
        if isinstance(usage_block, Mapping)
        else TokenUsage()
    )
    return ChatResponse(content="".join(text_parts), usage=usage, raw=body)


def _parse_google_response(body: Mapping[str, object]) -> ChatResponse:
    """Google ``generateContent`` returns ``candidates[0].content.parts[*].text``.

    Token usage is in ``usageMetadata.{promptTokenCount, candidatesTokenCount}``.
    """

    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LlmRequestError(
        f"Google response missing candidates: {body!r}",
        code="llm.malformed_response",
    )
    first = candidates[0]
    if not isinstance(first, Mapping):
        raise LlmRequestError(
        f"Malformed candidate: {first!r}",
        code="llm.malformed_response",
    )
    content = first.get("content")
    if not isinstance(content, Mapping):
        raise LlmRequestError(
        f"Malformed candidate content: {first!r}",
        code="llm.malformed_response",
    )
    parts = content.get("parts")
    text_parts: list[str] = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, Mapping):
                text_parts.append(str(part.get("text", "")))
    usage_meta = body.get("usageMetadata")
    usage = TokenUsage()
    if isinstance(usage_meta, Mapping):
        usage = TokenUsage(
            input_tokens=int(usage_meta.get("promptTokenCount", 0) or 0),
            output_tokens=int(
                usage_meta.get("candidatesTokenCount", 0) or 0
            ),
        )
    return ChatResponse(content="".join(text_parts), usage=usage, raw=body)


__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ChatTransport",
    "HttpxChatTransport",
    "LlmClient",
    "LlmRequestError",
    "NoApiKeyError",
    "TransportResult",
]
