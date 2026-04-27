"""Tests for ``transoria.bridge.handlers.prompts``."""

from __future__ import annotations

from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.prompts import register
from transoria.prompts import (
    DEFAULT_GLOSSARY_PRESET_ID,
    DEFAULT_TRANSLATION_PRESET_ID,
    PromptKind,
    PromptPresetStore,
)
from transoria.settings import SettingsStore


@pytest.fixture
def env(tmp_path: Path):
    settings_store = SettingsStore(path=tmp_path / "settings.json")
    router = BridgeRouter()
    register(router, cache_root=tmp_path, settings_store=settings_store)
    return router, tmp_path, settings_store


def test_list_returns_default_preset(env):
    router, _, _ = env

    response = router.call("prompts.list", {"kind": "translation"})

    assert response["active_id"] == DEFAULT_TRANSLATION_PRESET_ID
    summaries = response["presets"]
    assert any(p["is_default"] for p in summaries)


def test_list_invalid_kind_returns_invalid_argument(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call("prompts.list", {"kind": "translations"})

    assert caught.value.code == "bridge.invalid_argument"


def test_create_appends_preset(env):
    router, tmp_path, _ = env

    response = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Concise",
                "system_prompt": "Translate.",
                "suffix_prompt": "",
                "thinking_prompt": "",
                "description": "",
                "enabled": True,
            },
        },
    )

    new_id = response["preset"]["id"]
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json",
        kind=PromptKind.TRANSLATION,
    )
    assert any(p.id == new_id for p in store.load())


def test_read_returns_full_body(env):
    router, _, _ = env

    response = router.call(
        "prompts.read", {"id": DEFAULT_TRANSLATION_PRESET_ID}
    )

    assert response["preset"]["id"] == DEFAULT_TRANSLATION_PRESET_ID
    assert response["preset"]["system_prompt"]


def test_read_unknown_id_returns_not_found(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call("prompts.read", {"id": "missing"})

    assert caught.value.code == "bridge.not_found"


def test_update_changes_editable_field(env):
    router, _, _ = env

    response = router.call(
        "prompts.update",
        {
            "id": DEFAULT_TRANSLATION_PRESET_ID,
            "patch": {"description": "Updated"},
        },
    )

    assert response["preset"]["description"] == "Updated"


def test_update_rejects_id_change_on_default(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.update",
            {
                "id": DEFAULT_TRANSLATION_PRESET_ID,
                "patch": {"id": "renamed"},
            },
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_update_rejects_unknown_field(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.update",
            {
                "id": DEFAULT_TRANSLATION_PRESET_ID,
                "patch": {"system_prompt": "ok", "foo": "bar"},
            },
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_duplicate_creates_copy(env):
    router, _, _ = env

    response = router.call(
        "prompts.duplicate",
        {"id": DEFAULT_TRANSLATION_PRESET_ID, "new_name": "Variant"},
    )

    assert response["preset"]["name"] == "Variant"
    assert response["preset"]["is_default"] is False


def test_delete_default_preset_rejected(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.delete", {"id": DEFAULT_TRANSLATION_PRESET_ID}
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_delete_custom_preset_clears_active_when_set(env):
    router, _, settings_store = env
    new = router.call(
        "prompts.create",
        {
            "kind": "glossary",
            "preset": {
                "name": "Custom",
                "system_prompt": "x",
                "suffix_prompt": "",
                "thinking_prompt": "",
                "description": "",
                "enabled": True,
            },
        },
    )
    custom_id = new["preset"]["id"]
    router.call(
        "prompts.select_active",
        {"kind": "glossary", "preset_id": custom_id},
    )

    router.call("prompts.delete", {"id": custom_id})

    assert (
        settings_store.load_all().app.active_glossary_prompt_id is None
    )


def test_select_active_persists(env):
    router, _, settings_store = env

    response = router.call(
        "prompts.select_active",
        {"kind": "translation", "preset_id": DEFAULT_TRANSLATION_PRESET_ID},
    )

    assert (
        response["app"]["active_translation_prompt_id"]
        == DEFAULT_TRANSLATION_PRESET_ID
    )
    assert (
        settings_store.load_all().app.active_translation_prompt_id
        == DEFAULT_TRANSLATION_PRESET_ID
    )


def test_select_active_rejects_unknown(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.select_active",
            {"kind": "translation", "preset_id": "missing"},
        )

    assert caught.value.code == "bridge.not_found"


def test_preview_uses_build_prompt_substitution(env):
    router, _, _ = env

    response = router.call(
        "prompts.preview",
        {
            "preset_id": DEFAULT_TRANSLATION_PRESET_ID,
            "context": {
                "source_language": "Korean",
                "target_language": "Simplified Chinese",
            },
            "thinking": False,
        },
    )

    assert "Simplified Chinese" in response["prompt"]


def test_preview_thinking_includes_thinking_prompt(env):
    router, _, _ = env

    plain = router.call(
        "prompts.preview",
        {
            "preset_id": DEFAULT_TRANSLATION_PRESET_ID,
            "context": {
                "source_language": "Korean",
                "target_language": "zh",
            },
            "thinking": False,
        },
    )["prompt"]
    thinking = router.call(
        "prompts.preview",
        {
            "preset_id": DEFAULT_TRANSLATION_PRESET_ID,
            "context": {
                "source_language": "Korean",
                "target_language": "zh",
            },
            "thinking": True,
        },
    )["prompt"]

    assert thinking != plain
    assert "<why>" in thinking


def test_reset_to_default_restores_seed(env):
    router, _, _ = env
    router.call(
        "prompts.update",
        {
            "id": DEFAULT_GLOSSARY_PRESET_ID,
            "patch": {"system_prompt": "tampered"},
        },
    )

    response = router.call(
        "prompts.reset_to_default", {"id": DEFAULT_GLOSSARY_PRESET_ID}
    )

    assert response["preset"]["system_prompt"] != "tampered"


def test_reset_rejects_non_default_id(env):
    router, _, _ = env
    new = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Custom",
                "system_prompt": "x",
                "suffix_prompt": "",
                "thinking_prompt": "",
                "description": "",
                "enabled": True,
            },
        },
    )
    custom_id = new["preset"]["id"]

    with pytest.raises(BridgeError) as caught:
        router.call("prompts.reset_to_default", {"id": custom_id})

    assert caught.value.code == "bridge.not_found"
