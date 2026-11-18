"""GLM thinking controls, which differ by model generation and hosting API."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from transoria.llm.config import ModelConfig, ThinkingLevel

_LATEST_ALIAS = (99, 0)
_FIRST_FORCED_THINKING = (5, 3)
_FIRST_REASONING_EFFORT = (5, 2)
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
    host = (urlsplit(model.base_url).hostname or "").lower()
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return {"reasoning": _openrouter_reasoning(model, version)}
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


__all__ = ["glm_requires_thinking", "glm_thinking_fields", "glm_version"]
