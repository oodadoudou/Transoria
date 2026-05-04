"""JSON-backed settings persistence.

Atomic writes mirror :class:`transoria.prompts.PromptPresetStore`: temp
file alongside the target, then ``os.replace`` so a crash mid-write
cannot corrupt the user's settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Mapping

from transoria.settings.defaults import (
    _TRANSLATION_LIST_OF_MAPPING_FIELDS,
    AllSettings,
    AppSettings,
    GlossarySettings,
    GlossaryReviewSettings,
    ReplacementSettings,
    SettingsModule,
    TranslationSettings,
    default_module_settings,
    default_settings,
    merge_module,
    merge_module_lenient,
)

# Mid-rollout artefacts: the multi-profile experiment shipped briefly
# with ``*_model_ids`` tuples on disk. On hydrate we fold the first id
# back into the (now restored) single ``active_*_model_id`` so the
# user's selection survives the architecture revert.
_TUPLE_TO_SINGLE_APP_FIELDS: dict[str, str] = {
    "translation_model_ids": "active_translation_model_id",
    "glossary_model_ids": "active_glossary_model_id",
}

ModuleValue = (
    AppSettings
    | TranslationSettings
    | GlossarySettings
    | GlossaryReviewSettings
    | ReplacementSettings
)


@dataclass(frozen=True)
class SettingsStore:
    """Reads and writes ``settings.json`` under the cache directory."""

    path: Path

    def load_all(self) -> AllSettings:
        if not self.path.exists():
            return default_settings()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Cannot read settings file: {self.path}") from exc
        if not raw.strip():
            return default_settings()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Settings file is not valid JSON: {self.path}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"Settings file must contain a JSON object: {self.path}"
            )
        return _from_dict(payload)

    def save_all(self, settings: AllSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def save_partial(
        self, module: SettingsModule, patch: Mapping[str, object]
    ) -> AllSettings:
        current = self.load_all()
        module_value = _module_value(current, module)
        merged = merge_module(module_value, patch)
        updated = current.with_module(module, merged)
        self.save_all(updated)
        return updated

    def save_partial_lenient(
        self, module: SettingsModule, patch: Mapping[str, object]
    ) -> tuple[AllSettings, list[dict[str, str]]]:
        """Apply ``patch`` field-by-field; persist whichever fields
        accept their values and return ``(updated, rejected)``.

        Used by the user-facing settings bridge so a typo in one field
        doesn't throw away the user's other valid changes — see
        ``merge_module_lenient`` for the per-field semantics.
        """

        current = self.load_all()
        module_value = _module_value(current, module)
        merged, rejected = merge_module_lenient(module_value, patch)
        updated = current.with_module(module, merged)
        self.save_all(updated)
        return updated, rejected

    def reset_module(self, module: SettingsModule) -> ModuleValue:
        current = self.load_all()
        defaults = default_module_settings(module)
        updated = current.with_module(module, defaults)
        self.save_all(updated)
        return defaults


def _module_value(settings: AllSettings, module: SettingsModule) -> ModuleValue:
    if module == "app":
        return settings.app
    if module == "translation":
        return settings.translation
    if module == "glossary":
        return settings.glossary
    if module == "glossary_review":
        return settings.glossary_review
    if module == "replacement":
        return settings.replacement
    raise ValueError(f"Unknown settings module: {module!r}")


def _from_dict(payload: Mapping[str, object]) -> AllSettings:
    """Hydrate stored JSON into the typed bundle.

    Unknown keys are dropped silently (forward compatibility), and missing
    modules fall back to defaults so a partially-written file doesn't
    explode.
    """

    defaults = default_settings()
    return replace(
        defaults,
        app=_hydrate(AppSettings, payload.get("app"), defaults.app),
        translation=_hydrate(
            TranslationSettings, payload.get("translation"), defaults.translation
        ),
        glossary=_hydrate(
            GlossarySettings, payload.get("glossary"), defaults.glossary
        ),
        glossary_review=_hydrate(
            GlossaryReviewSettings,
            payload.get("glossary_review"),
            defaults.glossary_review,
        ),
        replacement=_hydrate(
            ReplacementSettings, payload.get("replacement"), defaults.replacement
        ),
    )


def _hydrate(
    cls: type, raw: object, fallback: ModuleValue
) -> ModuleValue:
    if not isinstance(raw, Mapping):
        return fallback
    valid_keys = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    init_kwargs = {**asdict(fallback)}
    for key, value in raw.items():
        if key in valid_keys:
            if (
                cls is TranslationSettings
                and key in _TRANSLATION_LIST_OF_MAPPING_FIELDS
                and isinstance(value, list)
            ):
                init_kwargs[key] = tuple(
                    dict(item) if isinstance(item, Mapping) else item
                    for item in value
                )
            else:
                init_kwargs[key] = value
    if cls is AppSettings:
        for tuple_field, single_field in _TUPLE_TO_SINGLE_APP_FIELDS.items():
            if tuple_field in raw and not init_kwargs.get(single_field):
                raw_value = raw[tuple_field]
                if isinstance(raw_value, list) and raw_value:
                    first = raw_value[0]
                    if isinstance(first, str) and first:
                        init_kwargs[single_field] = first
    return cls(**init_kwargs)


__all__ = ["SettingsStore"]
