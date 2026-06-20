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


def _seed_openai(store: ModelProfileStore) -> ModelConfig:
    """Create a single OpenAI-shaped profile for tests that need one.

    Step G removed first-run auto-seeding; tests now make their
    fixture explicit. Mirrors the legacy ``preset-openai`` shape so
    the assertions remain meaningful."""

    profile = ModelConfig(
        id="preset-openai",
        display_name="OpenAI",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://api.openai.com/v1",
        model_id="gpt-4o-mini",
    )
    return store.create(profile)


@pytest.fixture
def seeded_store(store: ModelProfileStore) -> ModelProfileStore:
    """Store with a single OpenAI profile and one DeepSeek profile.

    Used by tests that exercise multi-profile invariants."""

    _seed_openai(store)
    store.create(
        ModelConfig(
            id="preset-deepseek",
            display_name="DeepSeek",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-chat",
        )
    )
    store.create(
        ModelConfig(
            id="preset-google",
            display_name="Google",
            provider_format=ProviderFormat.GOOGLE,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model_id="gemini-2.5-flash",
        )
    )
    return store


def test_first_load_returns_empty_after_step_g(store, tmp_path):
    """Step G: fresh installs no longer auto-seed default profiles.
    The user walks through the Add API Profile modal to create their
    first profile from a template."""

    profiles = store.load()

    assert profiles == ()
    # The store does not write the file on first load — only when
    # the first ``create`` lands.
    assert not (tmp_path / "model_profiles.json").exists()


def test_default_profile_ids_constant_still_exposed():
    """``DEFAULT_PROFILE_IDS`` is kept for backward compatibility.
    Step G stops using it as a seeding source but other call sites
    may still reference the canonical legacy ids when migrating
    older user files."""

    assert "preset-openai" in DEFAULT_PROFILE_IDS
    assert "preset-deepseek" in DEFAULT_PROFILE_IDS


def test_default_profiles_helper_still_returns_legacy_seed():
    """The ``default_profiles()`` helper survives so a future migration
    pass can import the legacy seed shape if needed. Not auto-called
    by the store anymore."""

    profiles = default_profiles()
    assert {p.id for p in profiles} == set(DEFAULT_PROFILE_IDS)


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


def test_create_rejects_duplicate_id(seeded_store):
    with pytest.raises(ValueError, match="already exists"):
        seeded_store.create(
            ModelConfig(
                id="preset-openai",
                display_name="OpenAI duplicate",
                provider_format=ProviderFormat.OPENAI,
                base_url="https://api.openai.com/v1",
                model_id="gpt-4o-mini",
            )
        )


def test_update_modifies_field(seeded_store):
    seeded_store.update("preset-openai", {"display_name": "OpenAI (renamed)"})

    refreshed = seeded_store.get("preset-openai")
    assert refreshed is not None
    assert refreshed.display_name == "OpenAI (renamed)"


def test_update_rejects_unknown_field(seeded_store):
    with pytest.raises(ValueError, match="Unknown profile field"):
        seeded_store.update("preset-openai", {"nonexistent": 1})


def test_update_rejects_api_keys(seeded_store):
    with pytest.raises(ValueError, match="api_keys"):
        seeded_store.update("preset-openai", {"api_keys": ("sk-fake",)})


def test_delete_removes_profile_and_keys(seeded_store):
    seeded_store.set_api_keys("preset-openai", ("sk-test",))
    seeded_store.delete("preset-openai")

    assert seeded_store.get("preset-openai") is None
    keys_path = seeded_store.keys_path
    on_disk = json.loads(keys_path.read_text(encoding="utf-8"))
    assert "preset-openai" not in on_disk


def test_delete_unknown_profile_raises(store):
    with pytest.raises(KeyError):
        store.delete("does-not-exist")


def test_set_api_keys_persists_separately(seeded_store, tmp_path):
    seeded_store.set_api_keys("preset-deepseek", ("sk-1", "sk-2"))

    profile = seeded_store.get("preset-deepseek")
    assert profile is not None
    assert profile.api_keys == ("sk-1", "sk-2")

    profiles_payload = json.loads(
        (tmp_path / "model_profiles.json").read_text(encoding="utf-8")
    )
    for body in profiles_payload:
        assert body["api_keys"] == []


def test_api_key_status_reflects_state(seeded_store):
    assert seeded_store.api_key_status("preset-google") == "missing"
    seeded_store.set_api_keys("preset-google", ("sk-real",))
    assert seeded_store.api_key_status("preset-google") == "present"


def test_atomic_write_does_not_leave_tmp_files(seeded_store, tmp_path):
    seeded_store.set_api_keys("preset-openai", ("sk-x",))
    seeded_store.update("preset-openai", {"display_name": "Renamed"})

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert siblings == ["model_profile_keys.json", "model_profiles.json"]


def test_mask_api_keys_returns_last_four():
    assert mask_api_keys(("sk-fake12345",)) == "…2345"
    assert mask_api_keys(("a", "longerlast")) == "…last"
    assert mask_api_keys(()) == ""


def test_load_handles_corrupt_keys_file(seeded_store, tmp_path):
    (tmp_path / "model_profile_keys.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Keys file is not valid JSON"):
        seeded_store.load()
