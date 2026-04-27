"""Tests for ``transoria.model_profiles.store``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transoria.llm.config import ModelConfig, ProviderFormat
from transoria.model_profiles import (
    DEFAULT_PROFILE_IDS,
    ModelProfileStore,
    default_profiles,
    mask_api_keys,
)


@pytest.fixture
def store(tmp_path: Path) -> ModelProfileStore:
    return ModelProfileStore.from_cache_root(tmp_path)


def test_first_load_seeds_default_profiles(store, tmp_path):
    profiles = store.load()

    assert {p.id for p in profiles} == set(DEFAULT_PROFILE_IDS)
    assert (tmp_path / "model_profiles.json").exists()


def test_seeded_profiles_have_no_api_keys(store):
    profiles = store.load()

    assert all(p.api_keys == () for p in profiles)


def test_default_profile_match_defaults(store):
    profiles = store.load()
    expected = {p.id: p for p in default_profiles()}

    for profile in profiles:
        seed = expected[profile.id]
        assert profile.display_name == seed.display_name
        assert profile.base_url == seed.base_url
        assert profile.model_id == seed.model_id


def test_create_appends_new_profile(store):
    new_profile = ModelConfig(
        id="custom-volcengine",
        display_name="Volcengine Ark",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://ark.cn-beijing.volces.com/api/v3/",
        model_id="deepseek-v3-2-251201",
    )

    store.create(new_profile)
    profiles = store.load()

    assert any(p.id == "custom-volcengine" for p in profiles)


def test_create_rejects_duplicate_id(store):
    with pytest.raises(ValueError, match="already exists"):
        store.create(default_profiles()[0])


def test_update_modifies_field(store):
    store.update("preset-openai", {"display_name": "OpenAI (renamed)"})

    refreshed = store.get("preset-openai")
    assert refreshed is not None
    assert refreshed.display_name == "OpenAI (renamed)"


def test_update_rejects_unknown_field(store):
    with pytest.raises(ValueError, match="Unknown profile field"):
        store.update("preset-openai", {"nonexistent": 1})


def test_update_rejects_api_keys(store):
    with pytest.raises(ValueError, match="api_keys"):
        store.update("preset-openai", {"api_keys": ("sk-fake",)})


def test_delete_removes_profile_and_keys(store):
    store.set_api_keys("preset-openai", ("sk-test",))
    store.delete("preset-openai")

    assert store.get("preset-openai") is None
    keys_path = store.keys_path
    on_disk = json.loads(keys_path.read_text(encoding="utf-8"))
    assert "preset-openai" not in on_disk


def test_delete_unknown_profile_raises(store):
    with pytest.raises(KeyError):
        store.delete("does-not-exist")


def test_set_api_keys_persists_separately(store, tmp_path):
    store.set_api_keys("preset-deepseek", ("sk-1", "sk-2"))

    profile = store.get("preset-deepseek")
    assert profile is not None
    assert profile.api_keys == ("sk-1", "sk-2")

    profiles_payload = json.loads(
        (tmp_path / "model_profiles.json").read_text(encoding="utf-8")
    )
    for body in profiles_payload:
        assert body["api_keys"] == []


def test_api_key_status_reflects_state(store):
    assert store.api_key_status("preset-google") == "missing"
    store.set_api_keys("preset-google", ("sk-real",))
    assert store.api_key_status("preset-google") == "present"


def test_atomic_write_does_not_leave_tmp_files(store, tmp_path):
    store.set_api_keys("preset-openai", ("sk-x",))
    store.update("preset-openai", {"display_name": "Renamed"})

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert siblings == ["model_profile_keys.json", "model_profiles.json"]


def test_mask_api_keys_returns_last_four():
    assert mask_api_keys(("sk-fake12345",)) == "…2345"
    assert mask_api_keys(("a", "longerlast")) == "…last"
    assert mask_api_keys(()) == ""


def test_load_handles_corrupt_keys_file(store, tmp_path):
    (tmp_path / "model_profile_keys.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Keys file is not valid JSON"):
        store.load()
