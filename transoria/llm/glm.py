"""GLM thinking controls, which differ by model generation and hosting API."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from transoria.llm.config import ModelConfig, ThinkingLevel

_LATEST_ALIAS = (99, 0)
_FIRST_FORCED_THINKING = (5, 3)
_FIRST_REASONING_EFFORT = (5, 2)
_FIRST_PARTY_DOMAINS = ("bigmodel.cn", "z.ai")
_ANTHROPIC_MIN_THINKING_BUDGET = 1024
_GLM_ID = re.compile(
    r"(?:^|[/~:._-])glm-?(?:(?:flash-)?(?P<latest>latest)|(?P<major>\d+)(?:[.-](?P<minor>\d)(?!\d))?)",
    re.IGNORECASE,
)


def glm_version(model_id: str) -> tuple[int, int] | None:
    match = _GLM_ID.search(model_id)
    if match is None:
        return None
    if match["latest"]:
        return _LATEST_ALIAS
    return int(match["major"]), int(match["minor"] or 0)


def glm_requires_thinking(model_id: str) -> bool:
    version = glm_version(model_id)
    return version is not None and version >= _FIRST_FORCED_THINKING


def glm_thinking_fields(model: ModelConfig) -> dict[str, object] | None:
    """Request fields for a GLM model on an OpenAI-compatible API, or None for other models."""

    version = glm_version(model.model_id)
    if version is None:
        return None
    host = _host(model)
    if _on_domain(host, "openrouter.ai"):
        return {"reasoning": _openrouter_reasoning(model, version)}
    if _on_domain(host, "together.xyz", "together.ai"):
        return _together_fields(model, version)
    if _on_domain(host, "friendli.ai"):
        return _friendli_fields(model, version)
    effort = _effort(model.thinking_level, version)
    if "dashscope" in host:
        # DashScope reads enable_thinking; a thinking object would leave its default on.
        fields: dict[str, object] = {"enable_thinking": model.thinking_enabled}
        if model.thinking_enabled and version >= (5, 0):
            fields["reasoning_effort"] = effort
        return fields
    fields = {"thinking": {"type": "enabled" if model.thinking_enabled else "disabled"}}
    if model.thinking_enabled and version >= _FIRST_REASONING_EFFORT:
        fields["reasoning_effort"] = effort
    # Self-hosted servers and relays switch thinking through the model's chat template.
    if (
        not model.thinking_enabled
        and version < _FIRST_FORCED_THINKING
        and not _on_domain(host, *_FIRST_PARTY_DOMAINS)
    ):
        fields["chat_template_kwargs"] = {"enable_thinking": False}
    return fields


def glm_anthropic_fields(model: ModelConfig, max_tokens: int) -> dict[str, object] | None:
    """Thinking fields for a GLM model on an Anthropic-compatible API, or None for other models."""

    version = glm_version(model.model_id)
    if version is None:
        return None
    if not model.thinking_enabled:
        return {"thinking": {"type": "disabled"}}
    thinking: dict[str, object] = {"type": "enabled"}
    budget = max(model.effective_thinking_budget(), _ANTHROPIC_MIN_THINKING_BUDGET)
    if budget < max_tokens:
        thinking["budget_tokens"] = budget
    fields: dict[str, object] = {"thinking": thinking}
    # The budget does not set GLM's depth; DashScope's Anthropic mode rejects the effort field.
    if version >= _FIRST_REASONING_EFFORT and "dashscope" not in _host(model):
        fields["reasoning_effort"] = _effort(model.thinking_level, version)
    return fields


def _host(model: ModelConfig) -> str:
    return (urlsplit(model.base_url).hostname or "").lower()


def _on_domain(host: str, *domains: str) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _together_fields(model: ModelConfig, version: tuple[int, int]) -> dict[str, object]:
    if model.thinking_enabled and version >= _FIRST_FORCED_THINKING:
        return {"reasoning_effort": _effort(model.thinking_level, version)}
    return {"reasoning": {"enabled": model.thinking_enabled}}


def _friendli_fields(model: ModelConfig, version: tuple[int, int]) -> dict[str, object]:
    if version >= _FIRST_FORCED_THINKING:
        if not model.thinking_enabled:
            return {}
        return {"reasoning_effort": _effort(model.thinking_level, version)}
    fields: dict[str, object] = {
        "chat_template_kwargs": {"enable_thinking": model.thinking_enabled}
    }
    if model.thinking_enabled and version >= _FIRST_REASONING_EFFORT:
        fields["reasoning_effort"] = ThinkingLevel.HIGH.value
    return fields


def _effort(level: ThinkingLevel, version: tuple[int, int]) -> str:
    # GLM-5.3 and later reject every effort except low, high and max.
    if version >= _FIRST_FORCED_THINKING and level is ThinkingLevel.MEDIUM:
        return ThinkingLevel.HIGH.value
    return level.value


def _openrouter_reasoning(
    model: ModelConfig, version: tuple[int, int]
) -> dict[str, object]:
    if not model.thinking_enabled:
        return {"enabled": False}
    if version >= _FIRST_FORCED_THINKING:
        return {"effort": _effort(model.thinking_level, version)}
    if version >= _FIRST_REASONING_EFFORT:
        return {"effort": ThinkingLevel.HIGH.value}
    return {"enabled": True}


__all__ = [
    "glm_anthropic_fields",
    "glm_requires_thinking",
    "glm_thinking_fields",
    "glm_version",
]
