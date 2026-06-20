"""Tests for ``transoria.bridge.handlers.prompts``."""

from __future__ import annotations

from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.prompts import register
from transoria.model_profiles import ModelProfileStore
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
    profile_store = ModelProfileStore.from_cache_root(tmp_path)
    router = BridgeRouter()
    register(
        router,
        cache_root=tmp_path,
        settings_store=settings_store,
        profile_store=profile_store,
    )
    return router, tmp_path, settings_store, profile_store


def test_list_returns_default_preset(env):
    router, _, _, _ = env

    response = router.call("prompts.list", {"kind": "translation"})

    assert response["active_id"] == DEFAULT_TRANSLATION_PRESET_ID
    summaries = response["presets"]
    assert any(p["is_default"] for p in summaries)


def test_list_invalid_kind_returns_invalid_argument(env):
    router, _, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call("prompts.list", {"kind": "translations"})

    assert caught.value.code == "bridge.invalid_argument"


def test_create_appends_preset(env):
    router, tmp_path, _, _ = env

    response = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Concise",
                "system_prompt": "Translate.",
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


def test_create_ignores_suffix_prompt_payload(env):
    router, tmp_path, _, _ = env

    response = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "No Suffix",
                "system_prompt": "Translate.",
                "suffix_prompt": "bad user protocol",
                "thinking_prompt": "",
                "description": "",
                "enabled": True,
            },
        },
    )

    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json",
        kind=PromptKind.TRANSLATION,
    )
    persisted = next(p for p in store.load() if p.id == response["preset"]["id"])
    assert "suffix_prompt" not in response["preset"]
    assert persisted.suffix_prompt == ""


def test_create_ignores_thinking_prompt_payload(env):
    router, tmp_path, _, _ = env

    response = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "No Thinking",
                "system_prompt": "Translate.",
                "thinking_prompt": "user reasoning",
                "description": "",
                "enabled": True,
            },
        },
    )

    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json",
        kind=PromptKind.TRANSLATION,
    )
    persisted = next(p for p in store.load() if p.id == response["preset"]["id"])
    assert "thinking_prompt" not in response["preset"]
    assert persisted.thinking_prompt == ""


def test_read_returns_full_body(env):
    router, _, _, _ = env

    response = router.call(
        "prompts.read", {"id": DEFAULT_TRANSLATION_PRESET_ID}
    )

    assert response["preset"]["id"] == DEFAULT_TRANSLATION_PRESET_ID
    assert response["preset"]["system_prompt"]


def test_read_unknown_id_returns_not_found(env):
    router, _, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call("prompts.read", {"id": "missing"})

    assert caught.value.code == "bridge.not_found"


def test_update_changes_editable_field(env):
    router, _, _, _ = env
    custom = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Custom",
                "system_prompt": "x",
                "thinking_prompt": "",
                "description": "",
                "enabled": True,
            },
        },
    )["preset"]

    response = router.call(
        "prompts.update",
        {"id": custom["id"], "patch": {"description": "Updated"}},
    )

    assert response["preset"]["description"] == "Updated"


def test_update_rejects_system_preset(env):
    router, _, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.update",
            {
                "id": DEFAULT_TRANSLATION_PRESET_ID,
                "patch": {"description": "tampered"},
            },
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details.get("reason") == "is_system"


def test_update_rejects_id_change_on_default(env):
    router, _, _, _ = env

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
    router, _, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.update",
            {
                "id": DEFAULT_TRANSLATION_PRESET_ID,
                "patch": {"system_prompt": "ok", "foo": "bar"},
            },
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_update_rejects_suffix_prompt(env):
    router, _, _, _ = env
    custom = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Custom",
                "system_prompt": "x",
                "thinking_prompt": "",
                "description": "",
                "enabled": True,
            },
        },
    )["preset"]

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.update",
            {
                "id": custom["id"],
                "patch": {"suffix_prompt": "bad user protocol"},
            },
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details.get("field") == "suffix_prompt"


def test_update_rejects_thinking_prompt(env):
    router, _, _, _ = env
    custom = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Custom",
                "system_prompt": "x",
                "description": "",
                "enabled": True,
            },
        },
    )["preset"]

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.update",
            {
                "id": custom["id"],
                "patch": {"thinking_prompt": "bad user reasoning"},
            },
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details.get("field") == "thinking_prompt"


def test_duplicate_creates_copy(env):
    router, _, _, _ = env

    response = router.call(
        "prompts.duplicate",
        {"id": DEFAULT_TRANSLATION_PRESET_ID, "new_name": "Variant"},
    )

    assert response["preset"]["name"] == "Variant"
    assert response["preset"]["is_default"] is False
    assert response["preset"]["is_system"] is False


def test_duplicate_clears_suffix_prompt(env):
    router, tmp_path, _, _ = env

    response = router.call(
        "prompts.duplicate",
        {"id": DEFAULT_TRANSLATION_PRESET_ID, "new_name": "Variant"},
    )

    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json",
        kind=PromptKind.TRANSLATION,
    )
    copied = next(p for p in store.load() if p.id == response["preset"]["id"])
    assert copied.suffix_prompt == ""
    assert copied.thinking_prompt == ""


def test_delete_default_preset_rejected(env):
    router, _, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.delete", {"id": DEFAULT_TRANSLATION_PRESET_ID}
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_delete_custom_preset_clears_active_when_set(env):
    router, _, settings_store, _ = env
    new = router.call(
        "prompts.create",
        {
            "kind": "glossary",
            "preset": {
                "name": "Custom",
                "system_prompt": "x",
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
    router, _, settings_store, _ = env

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
    router, _, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "prompts.select_active",
            {"kind": "translation", "preset_id": "missing"},
        )

    assert caught.value.code == "bridge.not_found"


def test_preview_uses_build_prompt_substitution(env):
    router, _, _, _ = env

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


def test_preview_thinking_uses_system_guidance_not_payload(env):
    router, _, _, _ = env
    custom = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Thinking",
                "system_prompt": "Translate.",
                "thinking_prompt": "<why>\nthink first\n</why>",
                "description": "",
                "enabled": True,
            },
        },
    )["preset"]

    plain = router.call(
        "prompts.preview",
        {
            "preset_id": custom["id"],
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
            "preset_id": custom["id"],
            "context": {
                "source_language": "Korean",
                "target_language": "zh",
            },
            "thinking": True,
        },
    )["prompt"]

    assert thinking != plain
    assert "<why>" not in thinking
    assert "Before answering" in thinking


def test_preview_returns_clamped_false_when_no_active_model(env):
    """No active model → preview honors the requested ``thinking`` flag
    and reports ``clamped: false``. Architecture § 3.4 states the
    clamp only fires when the active profile is set to OFF."""

    router, _, _, _ = env
    response = router.call(
        "prompts.preview",
        {
            "preset_id": DEFAULT_TRANSLATION_PRESET_ID,
            "context": {"source_language": "Korean", "target_language": "zh"},
            "thinking": True,
        },
    )

    assert response["thinking"] is True
    assert response["clamped"] is False
    assert response["active_thinking_level"] is None


def test_preview_clamps_thinking_when_active_profile_is_off(env):
    """If the user picked an active model whose ``thinking_level`` is
    ``OFF``, preview must NOT render the thinking suffix even when the
    UI asks for it — otherwise the preview lies about what the runner
    will send."""

    from transoria.llm.config import ModelConfig, ProviderFormat, ThinkingLevel

    router, _, settings_store, profile_store = env

    profile = ModelConfig(
        id="custom-off",
        display_name="Custom Off",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://api.example.com",
        model_id="gpt-no-thinking",
        thinking_level=ThinkingLevel.OFF,
    )
    profile_store.create(profile)
    settings_store.save_partial(
        "app", {"active_translation_model_id": "custom-off"}
    )

    plain = router.call(
        "prompts.preview",
        {
            "preset_id": DEFAULT_TRANSLATION_PRESET_ID,
            "context": {"source_language": "Korean", "target_language": "zh"},
            "thinking": False,
        },
    )
    clamped = router.call(
        "prompts.preview",
        {
            "preset_id": DEFAULT_TRANSLATION_PRESET_ID,
            "context": {"source_language": "Korean", "target_language": "zh"},
            "thinking": True,
        },
    )

    assert clamped["thinking"] is False
    assert clamped["clamped"] is True
    assert clamped["active_thinking_level"] == "off"
    assert clamped["prompt"] == plain["prompt"]


def test_reset_to_default_returns_canonical_seed(env):
    router, _, _, _ = env

    # System presets are immutable, so reset_to_default is effectively a
    # read of the canonical seeded body — but the handler still exists
    # for the contract surface and must return the current seed.
    response = router.call(
        "prompts.reset_to_default", {"id": DEFAULT_GLOSSARY_PRESET_ID}
    )

    assert response["preset"]["id"] == DEFAULT_GLOSSARY_PRESET_ID
    assert response["preset"]["is_system"] is True
    assert response["preset"]["system_prompt"]


def test_reset_rejects_non_default_id(env):
    router, _, _, _ = env
    new = router.call(
        "prompts.create",
        {
            "kind": "translation",
            "preset": {
                "name": "Custom",
                "system_prompt": "x",
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
