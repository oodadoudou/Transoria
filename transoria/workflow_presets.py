"""Workflow preset storage."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

from transoria.domain import Language
from transoria.prompts import PromptKind


@dataclass(frozen=True)
class PresetRoute:
    model_profile_id: str
    prompt_preset_id: str
    concurrency: int = 1

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "PresetRoute":
        return cls(
            model_profile_id=str(data.get("model_profile_id", "")),
            prompt_preset_id=str(data.get("prompt_preset_id", "")),
            concurrency=int(data.get("concurrency", 1)),
        )


@dataclass(frozen=True)
class WorkflowPreset:
    id: str
    name: str
    kind: PromptKind
    model_profile_id: str
    prompt_preset_id: str
    source_language: str
    target_language: str
    enabled: bool = True
    advanced: bool = False
    routes: tuple[PresetRoute, ...] = ()
    fallback_route: PresetRoute | None = None
    group_concurrency: int = 0
    retry_failed: bool = False

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "WorkflowPreset":
        kind = PromptKind(str(data["kind"]))
        source_language = str(data.get("source_language", Language.KOREAN.value))
        target_language = str(
            data.get("target_language", Language.CHINESE_SIMPLIFIED.value)
        )
        Language(source_language)
        Language(target_language)
        raw_routes = data.get("routes", ())
        routes = (
            tuple(PresetRoute.from_dict(item) for item in raw_routes if isinstance(item, Mapping))
            if isinstance(raw_routes, (list, tuple))
            else ()
        )
        raw_fallback = data.get("fallback_route")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            kind=kind,
            model_profile_id=str(data.get("model_profile_id", "")),
            prompt_preset_id=str(data.get("prompt_preset_id", "")),
            source_language=source_language,
            target_language=target_language,
            enabled=bool(data.get("enabled", True)),
            advanced=bool(data.get("advanced", False)),
            routes=routes,
            fallback_route=(
                PresetRoute.from_dict(raw_fallback)
                if isinstance(raw_fallback, Mapping)
                else None
            ),
            group_concurrency=int(data.get("group_concurrency", 0)),
            retry_failed=bool(data.get("retry_failed", False)),
        )


@dataclass(frozen=True)
class WorkflowPresetStore:
    path: Path
    kind: PromptKind

    def load(self) -> tuple[WorkflowPreset, ...]:
        if not self.path.exists():
            return ()
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            return ()
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError(f"Workflow preset file must contain a list: {self.path}")
        presets = [WorkflowPreset.from_dict(item) for item in payload]
        wrong_kind = [preset.id for preset in presets if preset.kind is not self.kind]
        if wrong_kind:
            raise ValueError(
                f"Preset kind mismatch in {self.path}: {wrong_kind!r} != {self.kind.value}"
            )
        return tuple(presets)

    def save(self, presets: Sequence[WorkflowPreset]) -> None:
        wrong_kind = [preset.id for preset in presets if preset.kind is not self.kind]
        if wrong_kind:
            raise ValueError(
                f"Preset kind mismatch when saving: {wrong_kind!r} != {self.kind.value}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps([preset.to_dict() for preset in presets], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def replace_one(self, preset: WorkflowPreset) -> WorkflowPreset:
        presets = list(self.load())
        for index, current in enumerate(presets):
            if current.id == preset.id:
                presets[index] = preset
                self.save(presets)
                return preset
        self.save([*presets, preset])
        return preset

    def update_one(self, preset_id: str, **updates: object) -> WorkflowPreset | None:
        presets = list(self.load())
        for index, preset in enumerate(presets):
            if preset.id != preset_id:
                continue
            updated = replace(preset, **updates)
            presets[index] = updated
            self.save(presets)
            return updated
        return None

    def delete_one(self, preset_id: str) -> bool:
        presets = list(self.load())
        remaining = [preset for preset in presets if preset.id != preset_id]
        if len(remaining) == len(presets):
            return False
        self.save(remaining)
        return True


__all__ = ["PresetRoute", "WorkflowPreset", "WorkflowPresetStore"]
