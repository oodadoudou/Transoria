"""Tests for ``transoria.bridge.handlers.settings``."""

from __future__ import annotations

from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.settings import default_store, register
from transoria.settings import SettingsStore, default_settings


@pytest.fixture
def router(tmp_path: Path):
    store = SettingsStore(path=tmp_path / "settings.json")
    router = BridgeRouter()
    register(router, store=store)
    return router, store


def test_load_all_returns_defaults(router):
    bridge, _ = router

    response = bridge.call("settings.load_all", {})

    assert response == default_settings().to_dict()


def test_save_partial_persists_and_returns_timestamp(router):
    bridge, store = router

    response = bridge.call(
        "settings.save_partial",
        {"module": "translation", "patch": {"context_lines": 12}},
    )

    assert "saved_at" in response and isinstance(response["saved_at"], str)
    assert store.load_all().translation.context_lines == 12


def test_save_partial_unknown_module_returns_invalid_argument(router):
    bridge, _ = router

    with pytest.raises(BridgeError) as caught:
        bridge.call(
            "settings.save_partial",
            {"module": "fakemod", "patch": {}},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "module"


def test_save_partial_unknown_field_includes_field_name(router):
    bridge, _ = router

    with pytest.raises(BridgeError) as caught:
        bridge.call(
            "settings.save_partial",
            {"module": "translation", "patch": {"nope": 1}},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "nope"


def test_save_partial_wrong_type_includes_field_name(router):
    bridge, _ = router

    with pytest.raises(BridgeError) as caught:
        bridge.call(
            "settings.save_partial",
            {"module": "glossary", "patch": {"merge_folder_glossary": "yes"}},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "merge_folder_glossary"


def test_save_partial_requires_patch_object(router):
    bridge, _ = router

    with pytest.raises(BridgeError) as caught:
        bridge.call(
            "settings.save_partial",
            {"module": "app", "patch": "not-a-dict"},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "patch"


def test_reset_module_returns_default_module(router):
    bridge, store = router
    bridge.call(
        "settings.save_partial",
        {"module": "glossary", "patch": {"merge_folder_glossary": False}},
    )

    response = bridge.call("settings.reset_module", {"module": "glossary"})

    assert response["merge_folder_glossary"] is True
    assert store.load_all().glossary.merge_folder_glossary is True


def test_reset_module_rejects_unknown_module(router):
    bridge, _ = router

    with pytest.raises(BridgeError) as caught:
        bridge.call("settings.reset_module", {"module": "x"})

    assert caught.value.code == "bridge.invalid_argument"


def test_default_store_uses_settings_json_under_cache_root(tmp_path: Path):
    store = default_store(tmp_path)

    assert store.path == tmp_path / "settings.json"
