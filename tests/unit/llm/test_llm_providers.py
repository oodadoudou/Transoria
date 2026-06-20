from __future__ import annotations

from transoria.llm import (
    ProviderFormat,
    ThinkingLevel,
    VOLCENGINE_ARK_BASE_URL,
    VOLCENGINE_ARK_DEFAULT_MODEL_ID,
    resolve_api_keys,
    volcengine_ark_default,
)


def test_resolve_api_keys_prefers_config_over_env() -> None:
    keys = resolve_api_keys(
        config_keys=("from-config",),
        env_var="MY_KEY",
        env={"MY_KEY": "from-env"},
    )

    assert keys == ("from-config",)


def test_resolve_api_keys_falls_back_to_env_when_config_empty() -> None:
    keys = resolve_api_keys(
        config_keys=(),
        env_var="MY_KEY",
        env={"MY_KEY": "alpha\nbeta\n  \ngamma\n"},
    )

    assert keys == ("alpha", "beta", "gamma")


def test_resolve_api_keys_returns_empty_when_nothing_configured() -> None:
    assert resolve_api_keys(config_keys=(), env_var="MISSING", env={}) == ()


def test_volcengine_ark_default_uses_documented_endpoint_and_model() -> None:
    config = volcengine_ark_default(env={})

    assert config.base_url == VOLCENGINE_ARK_BASE_URL
    assert config.model_id == VOLCENGINE_ARK_DEFAULT_MODEL_ID
    assert config.provider_format is ProviderFormat.OPENAI
    assert config.thinking_level is ThinkingLevel.OFF
    assert config.api_keys == ()


def test_volcengine_ark_default_picks_up_env_keys() -> None:
    config = volcengine_ark_default(
        env={"TRANSORIA_VOLCENGINE_ARK_API_KEY": "env-key-1\nenv-key-2"}
    )

    assert config.api_keys == ("env-key-1", "env-key-2")


def test_volcengine_ark_default_accepts_explicit_keys_over_env() -> None:
    config = volcengine_ark_default(
        api_keys=("explicit",),
        env={"TRANSORIA_VOLCENGINE_ARK_API_KEY": "should-be-ignored"},
    )

    assert config.api_keys == ("explicit",)
