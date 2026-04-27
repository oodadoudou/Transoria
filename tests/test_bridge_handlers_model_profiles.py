"""Tests for ``transoria.bridge.handlers.model_profiles``."""

from __future__ import annotations

from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.model_profiles import register
from transoria.model_profiles import DEFAULT_PROFILE_IDS, ModelProfileStore
from transoria.settings import SettingsStore


@pytest.fixture
def env(tmp_path: Path):
    profile_store = ModelProfileStore.from_cache_root(tmp_path)
    settings_store = SettingsStore(path=tmp_path / "settings.json")
    router = BridgeRouter()
    register(router, profile_store=profile_store, settings_store=settings_store)
    return router, profile_store, settings_store


def test_list_returns_seeded_profiles(env):
    router, _, _ = env

    response = router.call("model_profiles.list", {})

    ids = {p["id"] for p in response["profiles"]}
    assert ids == set(DEFAULT_PROFILE_IDS)
    for profile in response["profiles"]:
        assert "api_keys" not in profile
        assert profile["api_key_status"] in {"missing", "present"}
        assert profile["api_key_masked"] == ""


def test_create_appends_new_profile(env):
    router, store, _ = env

    response = router.call(
        "model_profiles.create",
        {
            "profile": {
                "display_name": "Volcengine Ark",
                "provider_format": "openai",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3/",
                "model_id": "deepseek-v3-2-251201",
                "api_keys": ["sk-ark-test"],
            }
        },
    )

    profile = response["profile"]
    assert profile["display_name"] == "Volcengine Ark"
    assert profile["api_key_status"] == "present"
    assert profile["api_key_masked"].startswith("…")
    assert any(p.id == profile["id"] for p in store.load())


def test_create_rejects_duplicate_id_with_conflict(env):
    router, _, _ = env
    body = {
        "id": "preset-openai",
        "display_name": "x",
        "provider_format": "openai",
        "base_url": "x",
        "model_id": "x",
    }

    with pytest.raises(BridgeError) as caught:
        router.call("model_profiles.create", {"profile": body})

    assert caught.value.code == "bridge.conflict"


def test_update_persists_change(env):
    router, store, _ = env

    response = router.call(
        "model_profiles.update",
        {"id": "preset-openai", "patch": {"display_name": "OpenAI Pro"}},
    )

    assert response["profile"]["display_name"] == "OpenAI Pro"
    refreshed = store.get("preset-openai")
    assert refreshed is not None and refreshed.display_name == "OpenAI Pro"


def test_update_unknown_profile_returns_not_found(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.update",
            {"id": "missing", "patch": {"display_name": "x"}},
        )

    assert caught.value.code == "bridge.not_found"


def test_update_rejects_api_keys_field(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.update",
            {"id": "preset-openai", "patch": {"api_keys": ["sk-x"]}},
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_set_api_key_replaces_keys(env):
    router, store, _ = env

    response = router.call(
        "model_profiles.set_api_key",
        {"id": "preset-deepseek", "api_keys": ["sk-1", "sk-2"]},
    )

    assert response["profile"]["api_key_status"] == "present"
    assert store.api_key_status("preset-deepseek") == "present"


def test_select_active_updates_app_settings(env):
    router, _, settings_store = env

    response = router.call(
        "model_profiles.select_active",
        {"module": "translation", "profile_id": "preset-anthropic"},
    )

    assert response["app"]["active_translation_model_id"] == "preset-anthropic"
    assert (
        settings_store.load_all().app.active_translation_model_id
        == "preset-anthropic"
    )


def test_select_active_rejects_unknown_profile(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.select_active",
            {"module": "translation", "profile_id": "missing"},
        )

    assert caught.value.code == "bridge.not_found"


def test_select_active_clears_when_null(env):
    router, _, settings_store = env
    router.call(
        "model_profiles.select_active",
        {"module": "translation", "profile_id": "preset-openai"},
    )

    response = router.call(
        "model_profiles.select_active",
        {"module": "translation", "profile_id": None},
    )

    assert response["app"]["active_translation_model_id"] is None
    assert (
        settings_store.load_all().app.active_translation_model_id is None
    )


def test_delete_clears_active_selection(env):
    router, _, settings_store = env
    router.call(
        "model_profiles.select_active",
        {"module": "glossary", "profile_id": "preset-openai"},
    )

    router.call("model_profiles.delete", {"id": "preset-openai"})

    assert (
        settings_store.load_all().app.active_glossary_model_id is None
    )


def test_test_connection_returns_unsupported(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.test_connection",
            {"id": "preset-openai", "request_id": "rid-1"},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["reason"] == "unsupported"


def test_fetch_model_list_returns_unsupported(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.fetch_model_list",
            {"id": "preset-openai", "request_id": "rid-1"},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["reason"] == "unsupported"
