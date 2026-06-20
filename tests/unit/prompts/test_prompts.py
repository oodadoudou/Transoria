from __future__ import annotations

import json
from pathlib import Path

import pytest

from transoria.prompts import (
    DEFAULT_GLOSSARY_EN_ID,
    DEFAULT_GLOSSARY_PRESET_ID,
    DEFAULT_TRANSLATION_EN_ID,
    DEFAULT_TRANSLATION_PRESET_ID,
    PromptContext,
    PromptKind,
    PromptPreset,
    PromptPresetStore,
    build_prompt,
    default_preset,
)


_OPINIONATED_DEFAULT_TERMS = (
    "资深",
    "文学翻译",
    "流畅",
    "润色",
    "去翻译腔",
    "本地化",
    "意译",
    "文化专有",
    "命名实体",
    "三层过滤",
    "senior",
    "literary",
    "fluent",
    "polish",
    "translationese",
    "localize",
    "culture-specific",
    "named-entity",
    "three-filter",
    "gauntlet",
)


def test_default_translation_preset_uses_target_language_placeholder() -> None:
    preset = default_preset(PromptKind.TRANSLATION)

    assert preset.id == DEFAULT_TRANSLATION_PRESET_ID
    assert preset.kind is PromptKind.TRANSLATION
    assert "{target_language}" in preset.system_prompt
    assert preset.suffix_prompt
    assert preset.enabled is True


def test_default_glossary_preset_uses_target_language_placeholder() -> None:
    preset = default_preset(PromptKind.GLOSSARY)

    assert preset.id == DEFAULT_GLOSSARY_PRESET_ID
    assert preset.kind is PromptKind.GLOSSARY
    assert "{target_language}" in preset.system_prompt


def test_seeded_default_prompt_settings_stay_general(tmp_path: Path) -> None:
    presets = (
        *PromptPresetStore(
            path=tmp_path / "missing.translation.json", kind=PromptKind.TRANSLATION
        ).load(),
        *PromptPresetStore(
            path=tmp_path / "missing.glossary.json", kind=PromptKind.GLOSSARY
        ).load(),
    )

    assert {preset.id for preset in presets} == {
        DEFAULT_TRANSLATION_PRESET_ID,
        DEFAULT_TRANSLATION_EN_ID,
        DEFAULT_GLOSSARY_PRESET_ID,
        DEFAULT_GLOSSARY_EN_ID,
    }
    combined = "\n".join(
        f"{preset.system_prompt}\n{preset.thinking_prompt}\n{preset.description}"
        for preset in presets
    ).lower()
    for term in _OPINIONATED_DEFAULT_TERMS:
        assert term.lower() not in combined


def test_build_prompt_substitutes_known_placeholders() -> None:
    preset = PromptPreset(
        id="t",
        name="t",
        kind=PromptKind.TRANSLATION,
        system_prompt=(
            "from {source_language} to {target_language}\n"
            "context={context}\nglossary={glossary}\ninput={input}"
        ),
    )

    output = build_prompt(
        preset,
        PromptContext(
            source_language="Korean",
            target_language="Chinese",
            glossary="신해범 -> 申海范",
            context="prev line",
            input='{"0":"hello"}',
        ),
    )

    assert "from Korean to Chinese" in output
    assert "context=prev line" in output
    assert "glossary=신해범 -> 申海范" in output
    # Literal JSON braces in the input value must survive.
    assert '{"0":"hello"}' in output


def test_build_prompt_ignores_custom_suffix_prompt() -> None:
    preset = PromptPreset(
        id="t",
        name="t",
        kind=PromptKind.TRANSLATION,
        system_prompt="hello {target_language}",
        suffix_prompt="user-controlled protocol",
    )

    output = build_prompt(preset, PromptContext(target_language="Chinese"))

    assert output == "hello Chinese"


def test_build_prompt_preserves_literal_jsonl_braces_in_default_preset() -> None:
    preset = default_preset(PromptKind.TRANSLATION)

    output = build_prompt(preset, PromptContext(target_language="Chinese"))

    assert "Chinese" in output
    assert "{target_language}" not in output
    assert '{"<INDEX>":"<译文>"}' in output


def test_build_prompt_leaves_unknown_placeholders_intact() -> None:
    preset = PromptPreset(
        id="t",
        name="t",
        kind=PromptKind.TRANSLATION,
        system_prompt="hello {unknown_var} {target_language}",
    )

    output = build_prompt(preset, PromptContext(target_language="Chinese"))

    assert "{unknown_var}" in output
    assert "Chinese" in output


def test_store_returns_seeded_presets_when_file_missing(tmp_path: Path) -> None:
    from transoria.prompts import DEFAULT_TRANSLATION_EN_ID

    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )

    presets = store.load()

    assert len(presets) == 2
    assert presets[0].id == DEFAULT_TRANSLATION_PRESET_ID
    assert {p.id for p in presets} == {
        DEFAULT_TRANSLATION_PRESET_ID,
        DEFAULT_TRANSLATION_EN_ID,
    }


def test_store_round_trips_user_presets(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.glossary.json", kind=PromptKind.GLOSSARY
    )
    custom = PromptPreset(
        id="my-glossary",
        name="My Glossary",
        kind=PromptKind.GLOSSARY,
        system_prompt="extract terms into {target_language}",
        description="custom",
    )

    store.save([default_preset(PromptKind.GLOSSARY), custom])
    loaded = store.load()

    # All seeded variants are always re-injected; the user's custom
    # entry round-trips alongside them.
    ids = {p.id for p in loaded}
    assert DEFAULT_GLOSSARY_PRESET_ID in ids
    assert "my-glossary" in ids
    assert any(p.description == "custom" for p in loaded)


def test_store_reinjects_default_when_user_file_omits_it(tmp_path: Path) -> None:
    path = tmp_path / "prompts.translation.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "custom",
                    "name": "Custom",
                    "kind": "translation",
                    "system_prompt": "hi {target_language}",
                    "suffix_prompt": "",
                    "description": "",
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    store = PromptPresetStore(path=path, kind=PromptKind.TRANSLATION)

    loaded = store.load()

    assert loaded[0].id == DEFAULT_TRANSLATION_PRESET_ID
    assert any(p.id == "custom" for p in loaded)


def test_store_rejects_kind_mismatch_on_save(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )

    with pytest.raises(ValueError, match="Preset kind mismatch"):
        store.save([default_preset(PromptKind.GLOSSARY)])


def test_store_rejects_kind_mismatch_on_load(tmp_path: Path) -> None:
    path = tmp_path / "prompts.translation.json"
    path.write_text(
        json.dumps([default_preset(PromptKind.GLOSSARY).to_dict()]),
        encoding="utf-8",
    )
    store = PromptPresetStore(path=path, kind=PromptKind.TRANSLATION)

    with pytest.raises(ValueError, match="Preset kind mismatch"):
        store.load()


def test_store_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "prompts.translation.json"
    path.write_text("{not json", encoding="utf-8")
    store = PromptPresetStore(path=path, kind=PromptKind.TRANSLATION)

    with pytest.raises(ValueError, match="not valid JSON"):
        store.load()


def test_get_active_returns_selected_when_enabled(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )
    custom = PromptPreset(
        id="custom",
        name="Custom",
        kind=PromptKind.TRANSLATION,
        system_prompt="hello {target_language}",
        enabled=True,
    )
    store.save([default_preset(PromptKind.TRANSLATION), custom])

    assert store.get_active("custom").id == "custom"


def test_get_active_falls_back_to_default_when_selection_missing(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )

    assert store.get_active(None).id == DEFAULT_TRANSLATION_PRESET_ID
    assert store.get_active("does-not-exist").id == DEFAULT_TRANSLATION_PRESET_ID


def test_get_active_falls_back_to_default_when_selected_disabled(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )
    disabled = PromptPreset(
        id="disabled",
        name="Disabled",
        kind=PromptKind.TRANSLATION,
        system_prompt="hi {target_language}",
        enabled=False,
    )
    store.save([default_preset(PromptKind.TRANSLATION), disabled])

    assert store.get_active("disabled").id == DEFAULT_TRANSLATION_PRESET_ID


def test_translation_and_glossary_stores_are_independent(tmp_path: Path) -> None:
    translation_store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )
    glossary_store = PromptPresetStore(
        path=tmp_path / "prompts.glossary.json", kind=PromptKind.GLOSSARY
    )

    translation_store.save([default_preset(PromptKind.TRANSLATION)])
    glossary_store.save([default_preset(PromptKind.GLOSSARY)])

    assert translation_store.path.exists()
    assert glossary_store.path.exists()
    assert translation_store.path != glossary_store.path
    assert translation_store.load()[0].kind is PromptKind.TRANSLATION
    assert glossary_store.load()[0].kind is PromptKind.GLOSSARY


def test_preset_dict_round_trip() -> None:
    preset = default_preset(PromptKind.GLOSSARY)

    assert PromptPreset.from_dict(preset.to_dict()) == preset


def test_thinking_branch_uses_system_guidance_not_preset_text() -> None:
    preset = default_preset(PromptKind.TRANSLATION)

    plain = build_prompt(preset, PromptContext(target_language="Chinese"))
    thinking = build_prompt(
        preset, PromptContext(target_language="Chinese"), thinking=True
    )

    assert "<why>" not in plain
    assert thinking != plain
    assert "Before answering" in thinking


def test_thinking_branch_ignores_custom_reasoning_block() -> None:
    preset = PromptPreset(
        id="t",
        name="t",
        kind=PromptKind.TRANSLATION,
        system_prompt="hello {target_language}",
        suffix_prompt="output JSONL",
        thinking_prompt="<why>\nthink first\n</why>",
        is_system=True,
    )

    plain = build_prompt(preset, PromptContext(target_language="Chinese"))
    thinking = build_prompt(
        preset, PromptContext(target_language="Chinese"), thinking=True
    )

    assert "<why>" not in plain
    assert "<why>" not in thinking
    assert "Before answering" in thinking
    assert thinking.index("Before answering") < thinking.index("output JSONL")


def test_thinking_branch_falls_back_when_preset_has_no_thinking_text() -> None:
    preset = PromptPreset(
        id="t",
        name="t",
        kind=PromptKind.TRANSLATION,
        system_prompt="hello {target_language}",
        suffix_prompt="output JSONL",
        is_system=True,
    )

    output = build_prompt(preset, PromptContext(target_language="Chinese"), thinking=True)

    assert "Before answering" in output
    assert output.endswith("output JSONL")


def test_glossary_default_does_not_request_visible_thinking() -> None:
    preset = default_preset(PromptKind.GLOSSARY)

    assert preset.thinking_prompt == ""


def test_custom_thinking_prompt_does_not_affect_prompt_build(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )
    custom = PromptPreset(
        id="custom-thinking",
        name="Custom",
        kind=PromptKind.TRANSLATION,
        system_prompt="hi {target_language}",
        suffix_prompt="JSONL",
        thinking_prompt="reason first then answer",
    )

    store.save([default_preset(PromptKind.TRANSLATION), custom])
    loaded = store.load()

    reloaded_custom = next(p for p in loaded if p.id == "custom-thinking")
    output = build_prompt(
        reloaded_custom,
        PromptContext(target_language="Chinese"),
        thinking=True,
    )
    assert "reason first then answer" not in output
    assert "Before answering" in output
