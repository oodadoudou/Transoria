"""Provider/model configuration for the LLM client layer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping


class ProviderFormat(str, Enum):
    OPENAI = "openai"
    GOOGLE = "google"
    ANTHROPIC = "anthropic"
    SAKURA = "sakura"
    CUSTOM = "custom"


class ThinkingLevel(str, Enum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ModelConfig:
    """A single entry in the global Model Library.

    ``api_keys`` is intentionally a tuple so equality and hashability hold; one
    key per slot, and ``rotate_keys`` controls whether the client cycles
    through them on transient auth/rate-limit failures.

    Sampling overrides (``top_p``, ``temperature``, ``presence_penalty``,
    ``frequency_penalty``) are ``None`` by default — the client omits them
    from the request payload, letting the provider's own defaults apply. Set a
    float to send an explicit override.

    ``custom_headers`` is a tuple of ``(name, value)`` pairs merged into the
    HTTP request headers per call. Useful for vendor-specific auth schemes,
    request-id propagation, or feature flags.

    ``input_token_limit`` is ``0`` by default = unbounded. When > 0, callers
    may use it to truncate user prompts before sending; the limit is informational
    on the client itself.
    """

    id: str
    display_name: str
    provider_format: ProviderFormat
    base_url: str
    model_id: str
    api_keys: tuple[str, ...] = ()
    thinking_level: ThinkingLevel = ThinkingLevel.OFF
    timeout_seconds: float = 60.0
    concurrency_limit: int = 2
    rpm_limit: int = 60
    tpm_limit: int = 0
    rotate_keys: bool = True
    retry_attempts: int = 2
    retry_initial_backoff_seconds: float = 1.0
    retry_max_backoff_seconds: float = 30.0
    max_output_tokens: int = 4096
    thinking_budget_tokens: int = 4096
    input_token_limit: int = 0
    top_p: float | None = None
    temperature: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    custom_headers: tuple[tuple[str, str], ...] = ()
    # Opt-in: when ``True`` and the model itself doesn't expose a
    # native thinking mode (``thinking_level == OFF``), the runner
    # still injects the preset's ``thinking_prompt`` into the user
    # message so the model is steered through structured reasoning
    # before producing the final answer. We never send a provider-
    # specific thinking API field for forced-thinking calls — those
    # would 4xx on non-thinking models.
    force_thinking_enable: bool = False

    @property
    def thinking_enabled(self) -> bool:
        """True when the provider supports a native thinking mode and
        the user has switched it on. Drives the wire-level thinking
        payload (``_thinking_payload``, Anthropic ``thinking={...}``,
        Google ``thinkingConfig={...}``)."""
        return self.thinking_level is not ThinkingLevel.OFF

    @property
    def thinking_prompt_enabled(self) -> bool:
        """True when the runner should inject the preset's
        ``thinking_prompt`` into the user message — either because the
        model has a native thinking mode, or because the user opted
        into forced fake-thinking on a non-thinking model."""
        return self.thinking_enabled or self.force_thinking_enable

    def with_api_keys(self, keys: tuple[str, ...]) -> ModelConfig:
        return replace(self, api_keys=tuple(keys))

    def custom_headers_dict(self) -> dict[str, str]:
        """Return the custom headers as a plain dict for outgoing HTTP calls."""
        return {name: value for name, value in self.custom_headers}

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "provider_format": self.provider_format.value,
            "base_url": self.base_url,
            "model_id": self.model_id,
            "api_keys": list(self.api_keys),
            "thinking_level": self.thinking_level.value,
            "timeout_seconds": self.timeout_seconds,
            "concurrency_limit": self.concurrency_limit,
            "rpm_limit": self.rpm_limit,
            "tpm_limit": self.tpm_limit,
            "rotate_keys": self.rotate_keys,
            "retry_attempts": self.retry_attempts,
            "retry_initial_backoff_seconds": self.retry_initial_backoff_seconds,
            "retry_max_backoff_seconds": self.retry_max_backoff_seconds,
            "max_output_tokens": self.max_output_tokens,
            "thinking_budget_tokens": self.thinking_budget_tokens,
            "input_token_limit": self.input_token_limit,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "custom_headers": [list(pair) for pair in self.custom_headers],
            "force_thinking_enable": self.force_thinking_enable,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ModelConfig:
        try:
            provider_format = ProviderFormat(str(data["provider_format"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid provider_format: {data!r}") from exc
        try:
            thinking_level = ThinkingLevel(
                str(data.get("thinking_level", ThinkingLevel.OFF.value))
            )
        except ValueError as exc:
            raise ValueError(f"Invalid thinking_level: {data!r}") from exc
        api_keys_raw = data.get("api_keys", ())
        if not isinstance(api_keys_raw, (list, tuple)):
            raise ValueError(f"api_keys must be a list/tuple: {data!r}")
        custom_headers_raw = data.get("custom_headers", ())
        if not isinstance(custom_headers_raw, (list, tuple)):
            raise ValueError(f"custom_headers must be a list/tuple: {data!r}")
        custom_headers: tuple[tuple[str, str], ...] = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in custom_headers_raw
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        )
        return cls(
            id=str(data["id"]),
            display_name=str(data["display_name"]),
            provider_format=provider_format,
            base_url=str(data["base_url"]),
            model_id=str(data["model_id"]),
            api_keys=tuple(str(k) for k in api_keys_raw),
            thinking_level=thinking_level,
            timeout_seconds=float(data.get("timeout_seconds", 60.0)),
            concurrency_limit=int(data.get("concurrency_limit", 2)),
            rpm_limit=int(data.get("rpm_limit", 60)),
            tpm_limit=int(data.get("tpm_limit", 0)),
            rotate_keys=bool(data.get("rotate_keys", True)),
            retry_attempts=int(data.get("retry_attempts", 2)),
            retry_initial_backoff_seconds=float(
                data.get("retry_initial_backoff_seconds", 1.0)
            ),
            retry_max_backoff_seconds=float(
                data.get("retry_max_backoff_seconds", 30.0)
            ),
            max_output_tokens=int(data.get("max_output_tokens", 4096)),
            thinking_budget_tokens=int(data.get("thinking_budget_tokens", 4096)),
            input_token_limit=int(data.get("input_token_limit", 0)),
            top_p=_optional_float(data.get("top_p")),
            temperature=_optional_float(data.get("temperature")),
            presence_penalty=_optional_float(data.get("presence_penalty")),
            frequency_penalty=_optional_float(data.get("frequency_penalty")),
            custom_headers=custom_headers,
            force_thinking_enable=bool(data.get("force_thinking_enable", False)),
        )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


__all__ = ["ProviderFormat", "ThinkingLevel", "ModelConfig"]
