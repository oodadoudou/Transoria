"""Tests for ``transoria.bridge.handlers.workflow_presets``."""

from __future__ import annotations

from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.workflow_presets import register
from transoria.domain import Language
from transoria.llm.config import ModelConfig, ProviderFormat
from transoria.model_profiles import ModelProfileStore
from transoria.prompts import DEFAULT_TRANSLATION_PRESET_ID
from transoria.settings import SettingsStore


@pytest.fixture
def env(tmp_path: Path):
    settings_store = SettingsStore(path=tmp_path / "settings.json")
    profile_store = ModelProfileStore.from_cache_root(tmp_path)
    profile_store.create(
        ModelConfig(
            id="model-1",
            display_name="Model 1",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example.test/v1",
            model_id="example-model",
        )
    )
    router = BridgeRouter()
    register(
        router,
        cache_root=tmp_path,
        settings_store=settings_store,
        profile_store=profile_store,
    )
    return router, settings_store


def test_create_lists_and_matches_current_settings(env):
    router, settings_store = env
    settings_store.save_partial(
        "app",
        {
            "active_translation_model_id": "model-1",
            "active_translation_prompt_id": DEFAULT_TRANSLATION_PRESET_ID,
        },
    )

    created = router.call(
        "workflow_presets.create",
        {
            "kind": "translation",
            "preset": {
                "id": "kr-basic",
                "name": "Korean Basic",
                "model_profile_id": "model-1",
                "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
                "source_language": Language.KOREAN.value,
                "target_language": Language.CHINESE_SIMPLIFIED.value,
            },
        },
    )
    listed = router.call("workflow_presets.list", {"kind": "translation"})

    assert created["preset"]["id"] == "kr-basic"
    assert listed["matched_id"] == "kr-basic"
    assert [item["id"] for item in listed["presets"]] == ["kr-basic"]


def test_apply_updates_model_prompt_and_source_language(env):
    router, settings_store = env
    router.call(
        "workflow_presets.create",
        {
            "kind": "translation",
            "preset": {
                "id": "ja-basic",
                "name": "Japanese Basic",
                "model_profile_id": "model-1",
                "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
                "source_language": Language.JAPANESE.value,
                "target_language": Language.CHINESE_TRADITIONAL.value,
            },
        },
    )

    response = router.call(
        "workflow_presets.apply",
        {"kind": "translation", "id": "ja-basic"},
    )
    settings = settings_store.load_all()

    assert response["app"]["active_translation_model_id"] == "model-1"
    assert response["app"]["active_translation_prompt_id"] == DEFAULT_TRANSLATION_PRESET_ID
    assert response["settings"]["source_language"] == Language.JAPANESE.value
    assert response["settings"]["target_language"] == Language.CHINESE_TRADITIONAL.value
    assert settings.translation.source_language == Language.JAPANESE.value
    assert settings.translation.target_language == Language.CHINESE_TRADITIONAL.value


def test_create_rejects_missing_model(env):
    router, _settings_store = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "workflow_presets.create",
            {
                "kind": "translation",
                "preset": {
                    "name": "Broken",
                    "model_profile_id": "missing",
                    "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
                    "source_language": Language.KOREAN.value,
                    "target_language": Language.CHINESE_SIMPLIFIED.value,
                },
            },
        )

    assert caught.value.code == "bridge.not_found"
    assert caught.value.payload.details["field"] == "model_profile_id"


def test_advanced_translation_preset_applies_routes_without_changing_basic_match(env):
    router, settings_store = env
    model_store = ModelProfileStore.from_cache_root(settings_store.path.parent)
    model_store.create(
        ModelConfig(
            id="model-2",
            display_name="Model 2",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://other.test/v1",
            model_id="example-model",
        )
    )
    route = {
        "model_profile_id": "model-1",
        "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
        "concurrency": 2,
    }
    created = router.call(
        "workflow_presets.create",
        {
            "kind": "translation",
            "preset": {
                "id": "group-1",
                "name": "Group",
                "model_profile_id": "model-1",
                "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
                "source_language": Language.KOREAN.value,
                "target_language": Language.CHINESE_SIMPLIFIED.value,
                "advanced": True,
                "routes": [route, {**route, "model_profile_id": "model-2"}],
                "fallback_route": {**route, "model_profile_id": "model-2"},
                "group_concurrency": 3,
                "retry_failed": True,
            },
        },
    )
    router.call("workflow_presets.apply", {"kind": "translation", "id": "group-1"})
    listed = router.call("workflow_presets.list", {"kind": "translation"})

    assert created["preset"]["routes"][1]["model_profile_id"] == "model-2"
    assert listed["matched_id"] == "group-1"
    assert settings_store.load_all().app.active_translation_workflow_preset_id == "group-1"

    router.call(
        "workflow_presets.create",
        {
            "kind": "translation",
            "preset": {
                "id": "basic-1",
                "name": "Basic",
                "model_profile_id": "model-1",
                "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
                "source_language": Language.KOREAN.value,
                "target_language": Language.CHINESE_SIMPLIFIED.value,
            },
        },
    )
    router.call("workflow_presets.apply", {"kind": "translation", "id": "basic-1"})
    assert settings_store.load_all().app.active_translation_workflow_preset_id is None
    assert router.call("workflow_presets.list", {"kind": "translation"})["matched_id"] == "basic-1"


def test_advanced_preset_rejects_duplicate_normal_model(env):
    router, _ = env
    route = {
        "model_profile_id": "model-1",
        "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
        "concurrency": 1,
    }
    with pytest.raises(BridgeError) as caught:
        router.call(
            "workflow_presets.create",
            {
                "kind": "translation",
                "preset": {
                    "name": "Invalid",
                    "model_profile_id": "model-1",
                    "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
                    "source_language": "kr",
                    "target_language": "zh",
                    "advanced": True,
                    "routes": [route, route],
                    "group_concurrency": 2,
                },
            },
        )
    assert caught.value.code == "bridge.invalid_argument"


def test_advanced_preset_derives_basic_fields_from_primary_route(env):
    router, _ = env
    created = router.call(
        "workflow_presets.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Group",
                "source_language": "kr",
                "target_language": "zh",
                "advanced": True,
                "routes": [
                    {
                        "model_profile_id": "model-1",
                        "prompt_preset_id": DEFAULT_TRANSLATION_PRESET_ID,
                        "concurrency": 1,
                    }
                ],
                "group_concurrency": 1,
            },
        },
    )["preset"]

    assert created["model_profile_id"] == "model-1"
    assert created["prompt_preset_id"] == DEFAULT_TRANSLATION_PRESET_ID
