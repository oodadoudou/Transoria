from __future__ import annotations

import pytest

from transoria.llm import ModelConfig, ProviderFormat, ThinkingLevel


def _sample_config(**overrides: object) -> ModelConfig:
    base: dict[str, object] = dict(
        id="test",
        display_name="Test Model",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("k1", "k2"),
        thinking_level=ThinkingLevel.MEDIUM,
        timeout_seconds=42.0,
        concurrency_limit=4,
        rpm_limit=120,
        rotate_keys=True,
    )
    base.update(overrides)
    return ModelConfig(**base)  # type: ignore[arg-type]


def test_model_config_round_trip() -> None:
    original = _sample_config()

    assert ModelConfig.from_dict(original.to_dict()) == original


def test_thinking_enabled_property() -> None:
    assert _sample_config(thinking_level=ThinkingLevel.OFF).thinking_enabled is False
    assert _sample_config(thinking_level=ThinkingLevel.LOW).thinking_enabled is True
    assert _sample_config(thinking_level=ThinkingLevel.HIGH).thinking_enabled is True


def test_with_api_keys_returns_new_instance() -> None:
    original = _sample_config(api_keys=())
    updated = original.with_api_keys(("new",))

    assert original.api_keys == ()
    assert updated.api_keys == ("new",)
    assert updated is not original


def test_from_dict_rejects_invalid_provider_format() -> None:
    with pytest.raises(ValueError, match="Invalid provider_format"):
        ModelConfig.from_dict(
            {
                "id": "x",
                "display_name": "x",
                "provider_format": "not-a-provider",
                "base_url": "https://x",
                "model_id": "x",
            }
        )


def test_from_dict_rejects_invalid_thinking_level() -> None:
    with pytest.raises(ValueError, match="Invalid thinking_level"):
        ModelConfig.from_dict(
            {
                "id": "x",
                "display_name": "x",
                "provider_format": "openai",
                "base_url": "https://x",
                "model_id": "x",
                "thinking_level": "extreme",
            }
        )


def test_sampling_overrides_round_trip() -> None:
    original = _sample_config(
        top_p=0.9,
        temperature=0.7,
        presence_penalty=0.1,
        frequency_penalty=0.0,
    )

    restored = ModelConfig.from_dict(original.to_dict())

    assert restored.top_p == 0.9
    assert restored.temperature == 0.7
    assert restored.presence_penalty == 0.1
    assert restored.frequency_penalty == 0.0


def test_sampling_overrides_default_to_none() -> None:
    config = ModelConfig.from_dict(
        {
            "id": "x",
            "display_name": "x",
            "provider_format": "openai",
            "base_url": "https://x",
            "model_id": "x",
        }
    )

    assert config.top_p is None
    assert config.temperature is None
    assert config.presence_penalty is None
    assert config.frequency_penalty is None
    assert config.input_token_limit == 0
    assert config.custom_headers == ()


def test_custom_headers_round_trip_and_dict_view() -> None:
    original = _sample_config(
        custom_headers=(("X-Trace", "abc"), ("X-Tenant", "studio-1")),
    )

    restored = ModelConfig.from_dict(original.to_dict())

    assert restored.custom_headers == (("X-Trace", "abc"), ("X-Tenant", "studio-1"))
    assert restored.custom_headers_dict() == {"X-Trace": "abc", "X-Tenant": "studio-1"}


def test_input_token_limit_round_trip() -> None:
    original = _sample_config(input_token_limit=8192)

    restored = ModelConfig.from_dict(original.to_dict())

    assert restored.input_token_limit == 8192


def test_from_dict_defaults_thinking_level_to_off() -> None:
    config = ModelConfig.from_dict(
        {
            "id": "x",
            "display_name": "x",
            "provider_format": "openai",
            "base_url": "https://x",
            "model_id": "x",
        }
    )

    assert config.thinking_level is ThinkingLevel.OFF
    assert config.api_keys == ()
