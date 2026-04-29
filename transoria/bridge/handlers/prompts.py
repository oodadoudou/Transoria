"""``prompts.*`` bridge handlers.

Backed by :class:`PromptPresetStore` (one library per kind). The preview
RPC reuses :func:`build_prompt` so the frontend's preview is byte-
identical to what the runner sends.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from secrets import token_hex
from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.llm.config import ThinkingLevel
from transoria.model_profiles import ModelProfileStore
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
from transoria.settings import SettingsStore

ACTIVE_FIELD_BY_KIND = {
    "translation": "active_translation_prompt_id",
    "glossary": "active_glossary_prompt_id",
}

ACTIVE_MODEL_FIELD_BY_KIND = {
    PromptKind.TRANSLATION: "active_translation_model_id",
    PromptKind.GLOSSARY: "active_glossary_model_id",
}


def _expect_kind(payload: Mapping[str, object]) -> PromptKind:
    raw = payload.get("kind")
    if raw == "translation":
        return PromptKind.TRANSLATION
    if raw == "glossary":
        return PromptKind.GLOSSARY
    raise BridgeError.invalid_argument(
        "kind must be 'translation' or 'glossary'.",
        field="kind",
    )


def _default_id(kind: PromptKind) -> str:
    return (
        DEFAULT_TRANSLATION_PRESET_ID
        if kind is PromptKind.TRANSLATION
        else DEFAULT_GLOSSARY_PRESET_ID
    )


def _summary(preset: PromptPreset) -> dict[str, object]:
    return {
        "id": preset.id,
        "name": preset.name,
        "kind": preset.kind.value,
        "description": preset.description,
        "enabled": preset.enabled,
        "is_default": preset.id == _default_id(preset.kind),
    }


def _body(preset: PromptPreset) -> dict[str, object]:
    return {
        **_summary(preset),
        "system_prompt": preset.system_prompt,
        "suffix_prompt": preset.suffix_prompt,
        "thinking_prompt": preset.thinking_prompt,
    }


def _store_for(cache_root: Path, kind: PromptKind) -> PromptPresetStore:
    filename = (
        "prompts.translation.json"
        if kind is PromptKind.TRANSLATION
        else "prompts.glossary.json"
    )
    return PromptPresetStore(path=cache_root / filename, kind=kind)


def _resolve_active_thinking_level(
    *,
    settings_store: SettingsStore,
    profile_store: ModelProfileStore,
    kind: PromptKind,
) -> ThinkingLevel | None:
    """Return the active model profile's thinking_level for ``kind``,
    or ``None`` when no model is selected or the saved profile is
    gone. The caller treats ``None`` as 'no clamp; honor the
    requested flag'."""

    field = ACTIVE_MODEL_FIELD_BY_KIND[kind]
    settings = settings_store.load_all()
    profile_id = getattr(settings.app, field)
    if not isinstance(profile_id, str) or not profile_id:
        return None
    profile = profile_store.get(profile_id)
    if profile is None:
        return None
    return profile.thinking_level


def _build_handlers(
    cache_root: Path,
    settings_store: SettingsStore,
    profile_store: ModelProfileStore,
) -> dict[str, object]:
    def list_presets(payload: Mapping[str, object]) -> dict[str, object]:
        kind = _expect_kind(payload)
        store = _store_for(cache_root, kind)
        presets = store.load()
        active_id = (
            settings_store.load_all().app.active_translation_prompt_id
            if kind is PromptKind.TRANSLATION
            else settings_store.load_all().app.active_glossary_prompt_id
        )
        resolved = store.get_active(active_id).id
        return {
            "presets": [_summary(p) for p in presets],
            "active_id": resolved,
        }

    def read(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        for kind in (PromptKind.TRANSLATION, PromptKind.GLOSSARY):
            store = _store_for(cache_root, kind)
            for preset in store.load():
                if preset.id == preset_id:
                    return {"preset": _body(preset)}
        raise BridgeError.not_found(
            f"prompt preset {preset_id!r} does not exist."
        )

    def create(payload: Mapping[str, object]) -> dict[str, object]:
        kind = _expect_kind(payload)
        body = payload.get("preset")
        if not isinstance(body, Mapping):
            raise BridgeError.invalid_argument(
                "preset object is required.",
                field="preset",
            )
        store = _store_for(cache_root, kind)
        existing = list(store.load())
        new_id = str(body.get("id") or _generate_id(body, kind))
        if any(p.id == new_id for p in existing):
            raise BridgeError.conflict(
                f"prompt preset id already exists: {new_id!r}"
            )
        try:
            new_preset = PromptPreset(
                id=new_id,
                name=str(body["name"]),
                kind=kind,
                system_prompt=str(body.get("system_prompt", "")),
                suffix_prompt=str(body.get("suffix_prompt", "")),
                thinking_prompt=str(body.get("thinking_prompt", "")),
                description=str(body.get("description", "")),
                enabled=bool(body.get("enabled", True)),
            )
        except KeyError as exc:
            raise BridgeError.invalid_argument(
                f"missing required field: {exc.args[0]!r}",
                field=str(exc.args[0]),
            ) from exc
        store.save([*existing, new_preset])
        return {"preset": _body(new_preset)}

    def update(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        patch = payload.get("patch")
        if not isinstance(patch, Mapping):
            raise BridgeError.invalid_argument(
                "patch object is required.",
                field="patch",
            )
        for kind in (PromptKind.TRANSLATION, PromptKind.GLOSSARY):
            store = _store_for(cache_root, kind)
            presets = list(store.load())
            for index, preset in enumerate(presets):
                if preset.id != preset_id:
                    continue
                if preset.id == _default_id(kind):
                    forbidden = {"id", "kind"}
                    if forbidden & patch.keys():
                        raise BridgeError.invalid_argument(
                            "id/kind of the default preset cannot change.",
                        )
                updates = _validate_preset_patch(patch)
                merged = replace(preset, **updates)
                presets[index] = merged
                store.save(presets)
                return {"preset": _body(merged)}
        raise BridgeError.not_found(
            f"prompt preset {preset_id!r} does not exist."
        )

    def duplicate(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        new_name = payload.get("new_name")
        for kind in (PromptKind.TRANSLATION, PromptKind.GLOSSARY):
            store = _store_for(cache_root, kind)
            presets = list(store.load())
            for preset in presets:
                if preset.id != preset_id:
                    continue
                copy_id = _generate_id(
                    {"name": new_name or f"{preset.name} (copy)"}, kind
                )
                copied = replace(
                    preset,
                    id=copy_id,
                    name=str(new_name) if new_name else f"{preset.name} (copy)",
                )
                store.save([*presets, copied])
                return {"preset": _body(copied)}
        raise BridgeError.not_found(
            f"prompt preset {preset_id!r} does not exist."
        )

    def delete(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        for kind in (PromptKind.TRANSLATION, PromptKind.GLOSSARY):
            store = _store_for(cache_root, kind)
            presets = list(store.load())
            for preset in presets:
                if preset.id != preset_id:
                    continue
                if preset.id == _default_id(kind):
                    raise BridgeError.invalid_argument(
                        "default preset cannot be deleted.",
                    )
                remaining = [p for p in presets if p.id != preset_id]
                store.save(remaining)
                # Clear active selection if this was active.
                current = settings_store.load_all()
                field = ACTIVE_FIELD_BY_KIND[kind.value]
                if getattr(current.app, field) == preset_id:
                    settings_store.save_partial("app", {field: None})
                return {}
        raise BridgeError.not_found(
            f"prompt preset {preset_id!r} does not exist."
        )

    def select_active(payload: Mapping[str, object]) -> dict[str, object]:
        kind = _expect_kind(payload)
        preset_id = payload.get("preset_id")
        if preset_id is not None and not isinstance(preset_id, str):
            raise BridgeError.invalid_argument(
                "preset_id must be a string or null.",
                field="preset_id",
            )
        if isinstance(preset_id, str) and preset_id:
            store = _store_for(cache_root, kind)
            if not any(p.id == preset_id for p in store.load()):
                raise BridgeError.not_found(
                    f"prompt preset {preset_id!r} does not exist."
                )
        field = ACTIVE_FIELD_BY_KIND[kind.value]
        updated = settings_store.save_partial("app", {field: preset_id})
        from dataclasses import asdict  # noqa: PLC0415

        return {"app": asdict(updated.app)}

    def preview(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "preset_id")
        context_raw = payload.get("context")
        if not isinstance(context_raw, Mapping):
            raise BridgeError.invalid_argument(
                "context must be a JSON object.",
                field="context",
            )
        requested_thinking = bool(payload.get("thinking", False))
        for kind in (PromptKind.TRANSLATION, PromptKind.GLOSSARY):
            store = _store_for(cache_root, kind)
            for preset in store.load():
                if preset.id != preset_id:
                    continue
                # Clamp the requested ``thinking`` flag against the
                # active model profile's ``thinking_level``. If the
                # active profile is set to ``OFF``, preview must not
                # render the thinking suffix — that would lie about
                # what the runner actually sends.
                active_level = _resolve_active_thinking_level(
                    settings_store=settings_store,
                    profile_store=profile_store,
                    kind=kind,
                )
                effective_thinking = requested_thinking
                clamped = False
                if (
                    requested_thinking
                    and active_level is not None
                    and active_level is ThinkingLevel.OFF
                ):
                    effective_thinking = False
                    clamped = True
                ctx = PromptContext(
                    source_language=str(context_raw.get("source_language", "")),
                    target_language=str(context_raw.get("target_language", "")),
                    glossary=str(context_raw.get("glossary", "")),
                    context=str(context_raw.get("context", "")),
                    input=str(context_raw.get("input", "")),
                )
                rendered = build_prompt(preset, ctx, thinking=effective_thinking)
                return {
                    "prompt": rendered,
                    "thinking": effective_thinking,
                    "clamped": clamped,
                    "active_thinking_level": (
                        active_level.value if active_level is not None else None
                    ),
                }
        raise BridgeError.not_found(
            f"prompt preset {preset_id!r} does not exist."
        )

    def reset_to_default(payload: Mapping[str, object]) -> dict[str, object]:
        preset_id = expect_string(payload, "id")
        for kind in (PromptKind.TRANSLATION, PromptKind.GLOSSARY):
            if preset_id != _default_id(kind):
                continue
            store = _store_for(cache_root, kind)
            presets = list(store.load())
            seed = default_preset(kind)
            for index, preset in enumerate(presets):
                if preset.id == preset_id:
                    presets[index] = seed
                    store.save(presets)
                    return {"preset": _body(seed)}
            store.save([seed, *presets])
            return {"preset": _body(seed)}
        raise BridgeError.not_found(
            f"only default presets support reset; {preset_id!r} is not a default."
        )

    return {
        "prompts.list": list_presets,
        "prompts.read": read,
        "prompts.create": create,
        "prompts.update": update,
        "prompts.duplicate": duplicate,
        "prompts.delete": delete,
        "prompts.select_active": select_active,
        "prompts.preview": preview,
        "prompts.reset_to_default": reset_to_default,
    }


def _validate_preset_patch(
    patch: Mapping[str, object],
) -> dict[str, object]:
    valid = {
        "name",
        "system_prompt",
        "suffix_prompt",
        "thinking_prompt",
        "description",
        "enabled",
    }
    coerced: dict[str, object] = {}
    for key, value in patch.items():
        if key not in valid:
            raise BridgeError.invalid_argument(
                f"field {key!r} cannot be updated.",
                field=key,
            )
        if key == "enabled":
            if not isinstance(value, bool):
                raise BridgeError.invalid_argument(
                    "enabled must be a boolean.",
                    field=key,
                )
            coerced[key] = value
        else:
            if not isinstance(value, str):
                raise BridgeError.invalid_argument(
                    f"{key} must be a string.",
                    field=key,
                )
            coerced[key] = value
    return coerced


def _generate_id(body: Mapping[str, object], kind: PromptKind) -> str:
    seed = str(body.get("name") or kind.value)
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in seed).strip("-")
    if not slug:
        slug = kind.value
    return f"{slug}-{token_hex(3)}"


def register(
    router: BridgeRouter,
    *,
    cache_root: Path,
    settings_store: SettingsStore,
    profile_store: ModelProfileStore,
) -> None:
    handlers = _build_handlers(cache_root, settings_store, profile_store)
    for method, handler in handlers.items():
        router.register(method, handler)  # type: ignore[arg-type]


__all__ = ["register"]
