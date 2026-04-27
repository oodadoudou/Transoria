"""Built-in provider profiles and API key resolution."""

from __future__ import annotations

import os
from typing import Sequence

from transoria.llm.config import ModelConfig, ProviderFormat, ThinkingLevel


VOLCENGINE_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/"
VOLCENGINE_ARK_DEFAULT_MODEL_ID = "deepseek-v3-2-251201"

_VOLCENGINE_ARK_ENV_VAR = "TRANSORIA_VOLCENGINE_ARK_API_KEY"


def resolve_api_keys(
    config_keys: Sequence[str], env_var: str, *, env: dict[str, str] | None = None
) -> tuple[str, ...]:
    """Resolve API keys with config precedence and env-var fallback.

    Configured keys (typically loaded from a gitignored ``models.json`` in the
    app data directory) take precedence. When none are present, the named
    environment variable is read and split on newlines so multiple keys can be
    rotated. Empty/whitespace entries are dropped. The function never raises;
    the caller decides how to react to an empty result.
    """

    cleaned = tuple(key.strip() for key in config_keys if key and key.strip())
    if cleaned:
        return cleaned
    source = env if env is not None else os.environ
    raw = source.get(env_var, "")
    return tuple(line.strip() for line in raw.splitlines() if line.strip())


def volcengine_ark_default(
    api_keys: Sequence[str] = (),
    *,
    thinking_level: ThinkingLevel = ThinkingLevel.OFF,
    env: dict[str, str] | None = None,
) -> ModelConfig:
    """Pre-filled Volcengine Ark profile used for implementation testing.

    API keys are resolved at runtime via :func:`resolve_api_keys`; nothing is
    persisted to the repository. ``thinking_level`` defaults to ``OFF`` because
    the test model ``deepseek-v3-2-251201`` enables reasoning explicitly via
    the provider parameter only when requested.
    """

    keys = resolve_api_keys(api_keys, _VOLCENGINE_ARK_ENV_VAR, env=env)
    return ModelConfig(
        id="volcengine-ark-deepseek-v3-2",
        display_name="Volcengine Ark · DeepSeek V3.2",
        provider_format=ProviderFormat.OPENAI,
        base_url=VOLCENGINE_ARK_BASE_URL,
        model_id=VOLCENGINE_ARK_DEFAULT_MODEL_ID,
        api_keys=keys,
        thinking_level=thinking_level,
        timeout_seconds=120.0,
        concurrency_limit=2,
        rpm_limit=60,
        rotate_keys=True,
    )


__all__ = [
    "VOLCENGINE_ARK_BASE_URL",
    "VOLCENGINE_ARK_DEFAULT_MODEL_ID",
    "resolve_api_keys",
    "volcengine_ark_default",
]
