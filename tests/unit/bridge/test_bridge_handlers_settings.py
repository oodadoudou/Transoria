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


def test_save_partial_unknown_field_returned_in_rejected_list(router):
    """Unknown fields used to abort the entire save; now they're
    listed in ``rejected_fields`` so the user's other valid changes
    in the same patch still go through."""

    bridge, _ = router

    response = bridge.call(
        "settings.save_partial",
        {"module": "translation", "patch": {"nope": 1}},
    )

    rejected = response["rejected_fields"]
    assert any(item["field"] == "nope" for item in rejected)


def test_save_partial_wrong_type_returned_in_rejected_list(router):
    """Type mismatches are per-field warnings, not whole-patch
    failures — the user might be editing a numeric field and a typo
    on one field shouldn't lose the other in-flight changes."""

    bridge, _ = router

    response = bridge.call(
        "settings.save_partial",
        {"module": "glossary", "patch": {"merge_folder_glossary": "yes"}},
    )

    rejected = response["rejected_fields"]
    assert any(item["field"] == "merge_folder_glossary" for item in rejected)


def test_save_partial_keeps_valid_fields_when_one_is_invalid(router, tmp_path):
    """The whole point of lenient save: valid fields land on disk
    even when an unknown field is in the same patch."""

    bridge, _ = router

    response = bridge.call(
        "settings.save_partial",
        {
            "module": "translation",
            "patch": {
                "input_folder": str(tmp_path),
                "totally_unknown_field": "garbage",
            },
        },
    )

    assert response["rejected_fields"]
    state = bridge.call("settings.load_all", {})
    assert state["translation"]["input_folder"] == str(tmp_path)


def test_save_partial_requires_patch_object(router):
    bridge, _ = router

    with pytest.raises(BridgeError) as caught:
        bridge.call(
            "settings.save_partial",
            {"module": "app", "patch": "not-a-dict"},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "patch"


def test_save_partial_io_error_includes_settings_path(tmp_path: Path):
    class BrokenStore:
        path = tmp_path / "settings.json"

        def save_partial_lenient(self, module, patch):
            raise OSError("permission denied")

    bridge = BridgeRouter()
    register(bridge, store=BrokenStore())  # type: ignore[arg-type]

    with pytest.raises(BridgeError) as caught:
        bridge.call(
            "settings.save_partial",
            {"module": "app", "patch": {"ui_scale": 1.2}},
        )

    assert caught.value.code == "bridge.io_error"
    assert "Cannot save settings file" in str(caught.value)
    assert caught.value.payload.details["settings_path"] == str(
        tmp_path / "settings.json"
    )


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
