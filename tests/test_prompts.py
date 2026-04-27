from __future__ import annotations

import json
from pathlib import Path

import pytest

from transoria.prompts import (
    DEFAULT_GLOSSARY_PRESET_ID,
    DEFAULT_TRANSLATION_PRESET_ID,
    PromptContext,
    PromptKind,
    PromptPreset,
    PromptPresetStore,
    build_prompt,
    default_preset,
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


def test_build_prompt_substitutes_known_placeholders() -> None:
    preset = PromptPreset(
        id="t",
        name="t",
        kind=PromptKind.TRANSLATION,
        system_prompt="from {source_language} to {target_language}",
        suffix_prompt="context={context}\nglossary={glossary}\ninput={input}",
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


def test_build_prompt_preserves_literal_jsonl_braces_in_default_preset() -> None:
    preset = default_preset(PromptKind.TRANSLATION)

    output = build_prompt(preset, PromptContext(target_language="Chinese"))

    assert "into Chinese:" in output
    assert '{"<INDEX>":"<Translated Text>"}' in output


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


def test_store_returns_default_when_file_missing(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )

    presets = store.load()

    assert len(presets) == 1
    assert presets[0].id == DEFAULT_TRANSLATION_PRESET_ID


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

    assert {p.id for p in loaded} == {DEFAULT_GLOSSARY_PRESET_ID, "my-glossary"}
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


def test_thinking_branch_inserts_reasoning_block_when_enabled() -> None:
    preset = default_preset(PromptKind.TRANSLATION)

    plain = build_prompt(preset, PromptContext(target_language="Chinese"))
    thinking = build_prompt(
        preset, PromptContext(target_language="Chinese"), thinking=True
    )

    assert "<why>" not in plain
    assert "<why>" in thinking
    # Thinking block must sit between system body and JSONL suffix.
    assert thinking.index("<why>") < thinking.index('{"<INDEX>":"<Translated Text>"}')


def test_thinking_branch_falls_back_when_preset_has_no_thinking_text() -> None:
    preset = PromptPreset(
        id="t",
        name="t",
        kind=PromptKind.TRANSLATION,
        system_prompt="hello {target_language}",
        suffix_prompt="output JSONL",
    )

    output = build_prompt(preset, PromptContext(target_language="Chinese"), thinking=True)

    assert output == "hello Chinese\n\noutput JSONL"


def test_glossary_default_has_thinking_prompt() -> None:
    preset = default_preset(PromptKind.GLOSSARY)

    assert preset.thinking_prompt
    assert "<why>" in preset.thinking_prompt


def test_thinking_prompt_persists_through_save_load(tmp_path: Path) -> None:
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
    assert reloaded_custom.thinking_prompt == "reason first then answer"
