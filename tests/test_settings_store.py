"""Tests for ``transoria.settings``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transoria.settings import (
    AllSettings,
    GlossarySettings,
    SettingsStore,
    TranslationSettings,
    default_module_settings,
    default_settings,
)


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(path=tmp_path / "settings.json")


def test_load_all_returns_defaults_for_missing_file(store):
    settings = store.load_all()

    assert settings == default_settings()


def test_load_all_returns_defaults_for_empty_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("", encoding="utf-8")
    store = SettingsStore(path=path)

    assert store.load_all() == default_settings()


def test_save_partial_writes_diff_atomically(store):
    updated = store.save_partial("translation", {"context_lines": 8})

    assert updated.translation.context_lines == 8
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["translation"]["context_lines"] == 8
    # Other modules untouched
    assert on_disk["glossary"]["merge_folder_glossary"] is True
    assert on_disk["app"]["interface_language"] == "en"


def test_save_partial_rejects_unknown_field(store):
    with pytest.raises(ValueError, match="Unknown settings field"):
        store.save_partial("translation", {"nonexistent": 1})


def test_save_partial_rejects_wrong_type(store):
    with pytest.raises(ValueError, match="expects a boolean"):
        store.save_partial("glossary", {"merge_folder_glossary": "yes"})


def test_reset_module_restores_defaults(store):
    store.save_partial("glossary", {"merge_folder_glossary": False})
    assert store.load_all().glossary.merge_folder_glossary is False

    restored = store.reset_module("glossary")

    assert restored == GlossarySettings()
    assert store.load_all().glossary == GlossarySettings()


def test_reset_module_does_not_disturb_other_modules(store):
    store.save_partial("translation", {"context_lines": 5})
    store.save_partial("glossary", {"minimum_frequency": 7})

    store.reset_module("glossary")

    assert store.load_all().translation.context_lines == 5
    assert store.load_all().glossary == default_module_settings("glossary")


def test_load_all_drops_unknown_keys_silently(tmp_path):
    path = tmp_path / "settings.json"
    payload = {
        "translation": {"context_lines": 10, "future_field": "ignored"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = SettingsStore(path=path)

    settings = store.load_all()

    assert settings.translation.context_lines == 10
    # Unknown key did not blow up; other modules came from defaults.
    assert settings.app == default_module_settings("app")


def test_load_all_falls_back_when_module_is_not_a_mapping(tmp_path):
    path = tmp_path / "settings.json"
    payload = {"translation": "broken"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = SettingsStore(path=path)

    settings = store.load_all()

    assert settings.translation == TranslationSettings()


def test_save_partial_atomic_write_does_not_leave_tmp(store):
    store.save_partial("app", {"theme": "dark"})

    siblings = list(store.path.parent.iterdir())
    assert siblings == [store.path]


def test_default_settings_match_contract():
    settings = default_settings()

    assert settings.glossary.merge_folder_glossary is True
    assert settings.glossary.keep_identical_src_dst is False
    assert settings.translation.bilingual_dedupe_identical is True
    assert settings.app.interface_language == "en"
    assert settings.replacement.apply_to_epub_titles is True


def test_all_settings_to_dict_round_trips(tmp_path):
    settings = AllSettings()
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings.to_dict()), encoding="utf-8")
    store = SettingsStore(path=path)

    assert store.load_all() == settings
