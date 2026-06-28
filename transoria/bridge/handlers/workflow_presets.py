"""``workflow_presets.*`` bridge handlers."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from secrets import token_hex
from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.domain import Language
from transoria.model_profiles import ModelProfileStore
from transoria.prompts import PromptKind, PromptPresetStore
from transoria.settings import SettingsStore
from transoria.workflow_presets import WorkflowPreset, WorkflowPresetStore

ACTIVE_MODEL_FIELD_BY_KIND = {
    PromptKind.TRANSLATION: "active_translation_model_id",
    PromptKind.GLOSSARY: "active_glossary_model_id",
    PromptKind.GLOSSARY_REVIEW: "active_glossary_review_model_id",
}

ACTIVE_PROMPT_FIELD_BY_KIND = {
    PromptKind.TRANSLATION: "active_translation_prompt_id",
    PromptKind.GLOSSARY: "active_glossary_prompt_id",
    PromptKind.GLOSSARY_REVIEW: "active_glossary_review_prompt_id",
}

SETTINGS_MODULE_BY_KIND = {
    PromptKind.TRANSLATION: "translation",
    PromptKind.GLOSSARY: "glossary",
    PromptKind.GLOSSARY_REVIEW: "glossary_review",
}


def _expect_kind(payload: Mapping[str, object]) -> PromptKind:
    raw = payload.get("kind")
    if raw == "translation":
        return PromptKind.TRANSLATION
    if raw == "glossary":
        return PromptKind.GLOSSARY
    if raw == "glossary_review":
        return PromptKind.GLOSSARY_REVIEW
    raise BridgeError.invalid_argument(
        "kind must be 'translation', 'glossary', or 'glossary_review'.",
        field="kind",
    )


def _store_for(cache_root: Path, kind: PromptKind) -> WorkflowPresetStore:
    return WorkflowPresetStore(
        path=cache_root / f"workflow_presets.{kind.value}.json",
        kind=kind,
    )


def _prompt_store_for(cache_root: Path, kind: PromptKind) -> PromptPresetStore:
    return PromptPresetStore(path=cache_root / f"prompts.{kind.value}.json", kind=kind)


def _summary(preset: WorkflowPreset) -> dict[str, object]:
    return preset.to_dict()


def _validate_references(
    *,
    cache_root: Path,
    profile_store: ModelProfileStore,
    kind: PromptKind,
    model_profile_id: str,
    prompt_preset_id: str,
    source_language: str,
    target_language: str,
) -> None:
    if profile_store.get(model_profile_id) is None:
        raise BridgeError.not_found(
            f"model profile {model_profile_id!r} does not exist.",
            details={"field": "model_profile_id", "id": model_profile_id},
        )
    prompt_store = _prompt_store_for(cache_root, kind)
    if not any(preset.id == prompt_preset_id for preset in prompt_store.load()):
        raise BridgeError.not_found(
            f"prompt preset {prompt_preset_id!r} does not exist.",
            details={"field": "prompt_preset_id", "id": prompt_preset_id},
        )
    try:
        Language(source_language)
    except ValueError as exc:
        raise BridgeError.invalid_argument(
            f"unsupported source_language: {source_language!r}",
            field="source_language",
        ) from exc
    try:
        Language(target_language)
    except ValueError as exc:
        raise BridgeError.invalid_argument(
            f"unsupported target_language: {target_language!r}",
            field="target_language",
        ) from exc


def _coerce_preset(
    *,
    cache_root: Path,
    profile_store: ModelProfileStore,
    kind: PromptKind,
    preset_id: str,
    body: Mapping[str, object],
) -> WorkflowPreset:
    name = str(body.get("name", "")).strip()
    if not name:
        raise BridgeError.invalid_argument("name is required.", field="name")
    model_profile_id = str(body.get("model_profile_id", "")).strip()
    prompt_preset_id = str(body.get("prompt_preset_id", "")).strip()
    source_language = str(body.get("source_language", "")).strip()
    target_language = str(
        body.get("target_language", Language.CHINESE_SIMPLIFIED.value)
    ).strip()
    _validate_references(
        cache_root=cache_root,
        profile_store=profile_store,
        kind=kind,
        model_profile_id=model_profile_id,
        prompt_preset_id=prompt_preset_id,
        source_language=source_language,
        target_language=target_language,
    )
    return WorkflowPreset(
        id=preset_id,
        name=name,
        kind=kind,
        model_profile_id=model_profile_id,
        prompt_preset_id=prompt_preset_id,
        source_language=source_language,
        target_language=target_language,
        enabled=bool(body.get("enabled", True)),
    )


def _generate_id(body: Mapping[str, object], kind: PromptKind) -> str:
    seed = str(body.get("name") or kind.value)
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in seed).strip("-")
    slug = "-".join(part for part in slug.split("-") if part) or kind.value
    return f"{kind.value}-{slug}-{token_hex(3)}"


def _matched_id(
    *, settings_store: SettingsStore, presets: tuple[WorkflowPreset, ...], kind: PromptKind
) -> str | None:
    settings = settings_store.load_all()
    module_settings = getattr(settings, SETTINGS_MODULE_BY_KIND[kind])
    model_id = getattr(settings.app, ACTIVE_MODEL_FIELD_BY_KIND[kind])
    prompt_id = getattr(settings.app, ACTIVE_PROMPT_FIELD_BY_KIND[kind])
    source_language = getattr(module_settings, "source_language", "")
    target_language = getattr(module_settings, "target_language", "")
    for preset in presets:
        if not preset.enabled:
            continue
        if (
            preset.model_profile_id == model_id
            and preset.prompt_preset_id == prompt_id
            and preset.source_language == source_language
            and preset.target_language == target_language
        ):
            return preset.id
    return None


def _build_handlers(
    *,
    cache_root: Path,
    settings_store: SettingsStore,
    profile_store: ModelProfileStore,
) -> dict[str, object]:
    def list_presets(payload: Mapping[str, object]) -> dict[str, object]:
        kind = _expect_kind(payload)
        presets = _store_for(cache_root, kind).load()
        return {
            "presets": [_summary(preset) for preset in presets],
            "matched_id": _matched_id(
                settings_store=settings_store, presets=presets, kind=kind
            ),
        }

    def create(payload: Mapping[str, object]) -> dict[str, object]:
        kind = _expect_kind(payload)
        body = payload.get("preset")
        if not isinstance(body, Mapping):
            raise BridgeError.invalid_argument("preset object is required.", field="preset")
        store = _store_for(cache_root, kind)
        preset_id = str(body.get("id") or _generate_id(body, kind))
        if any(preset.id == preset_id for preset in store.load()):
            raise BridgeError.conflict(f"workflow preset id already exists: {preset_id!r}")
        preset = _coerce_preset(
            cache_root=cache_root,
            profile_store=profile_store,
            kind=kind,
            preset_id=preset_id,
            body=body,
        )
        store.replace_one(preset)
        return {"preset": _summary(preset)}

    def update(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        patch = payload.get("patch")
        if not isinstance(patch, Mapping):
            raise BridgeError.invalid_argument("patch object is required.", field="patch")
        for kind in PromptKind:
            store = _store_for(cache_root, kind)
            current = next((preset for preset in store.load() if preset.id == preset_id), None)
            if current is None:
                continue
            body = {**current.to_dict(), **dict(patch)}
            updated = _coerce_preset(
                cache_root=cache_root,
                profile_store=profile_store,
                kind=kind,
                preset_id=preset_id,
                body=body,
            )
            store.replace_one(updated)
            return {"preset": _summary(updated)}
        raise BridgeError.not_found(f"workflow preset {preset_id!r} does not exist.")

    def duplicate(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        new_name = payload.get("new_name")
        for kind in PromptKind:
            store = _store_for(cache_root, kind)
            presets = store.load()
            for preset in presets:
                if preset.id != preset_id:
                    continue
                copied = replace(
                    preset,
                    id=_generate_id({"name": new_name or preset.name}, kind),
                    name=str(new_name) if new_name else f"{preset.name} (copy)",
                )
                store.replace_one(copied)
                return {"preset": _summary(copied)}
        raise BridgeError.not_found(f"workflow preset {preset_id!r} does not exist.")

    def delete(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        for kind in PromptKind:
            if _store_for(cache_root, kind).delete_one(preset_id):
                return {}
        raise BridgeError.not_found(f"workflow preset {preset_id!r} does not exist.")

    def apply(payload: Mapping[str, object]) -> dict[str, object]:
        kind = _expect_kind(payload)
        preset_id = expect_string(payload, "id")
        store = _store_for(cache_root, kind)
        preset = next((item for item in store.load() if item.id == preset_id), None)
        if preset is None:
            raise BridgeError.not_found(f"workflow preset {preset_id!r} does not exist.")
        _validate_references(
            cache_root=cache_root,
            profile_store=profile_store,
            kind=kind,
            model_profile_id=preset.model_profile_id,
            prompt_preset_id=preset.prompt_preset_id,
            source_language=preset.source_language,
            target_language=preset.target_language,
        )
        settings_store.save_partial(
            "app",
            {
                ACTIVE_MODEL_FIELD_BY_KIND[kind]: preset.model_profile_id,
                ACTIVE_PROMPT_FIELD_BY_KIND[kind]: preset.prompt_preset_id,
            },
        )
        updated = settings_store.save_partial(
            SETTINGS_MODULE_BY_KIND[kind],
            {
                "source_language": preset.source_language,
                "target_language": preset.target_language,
            },
        )
        module_name = SETTINGS_MODULE_BY_KIND[kind]
        return {
            "app": asdict(updated.app),
            "settings": asdict(getattr(updated, module_name)),
        }

    return {
        "workflow_presets.list": list_presets,
        "workflow_presets.create": create,
        "workflow_presets.update": update,
        "workflow_presets.duplicate": duplicate,
        "workflow_presets.delete": delete,
        "workflow_presets.apply": apply,
    }


def register(
    router: BridgeRouter,
    *,
    cache_root: Path,
    settings_store: SettingsStore,
    profile_store: ModelProfileStore,
) -> None:
    for method, handler in _build_handlers(
        cache_root=cache_root,
        settings_store=settings_store,
        profile_store=profile_store,
    ).items():
        router.register(method, handler)


__all__ = ["register"]
