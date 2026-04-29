"""JSON-backed model profile persistence.

Two files under the cache root:

- ``model_profiles.json`` — array of profile bodies (no API keys).
- ``model_profile_keys.json`` — `{profile_id: [keys...]}` map; gitignored
  in the repository root and meant to live alongside settings.json.

Atomic writes mirror the prompt preset / settings stores.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Mapping, Sequence

from transoria.llm.config import ModelConfig
from transoria.model_profiles.defaults import DEFAULT_PROFILE_IDS

ApiKeyStatus = Literal["missing", "present", "from_env"]

_PROFILES_FILENAME = "model_profiles.json"
_KEYS_FILENAME = "model_profile_keys.json"


@dataclass(frozen=True)
class ModelProfileStore:
    """Reads and writes model profiles + their (separate) API keys."""

    profiles_path: Path
    keys_path: Path

    @classmethod
    def from_cache_root(cls, cache_root: Path) -> "ModelProfileStore":
        return cls(
            profiles_path=cache_root / _PROFILES_FILENAME,
            keys_path=cache_root / _KEYS_FILENAME,
        )

    def load(self) -> tuple[ModelConfig, ...]:
        bodies = self._load_bodies()
        keys = self._load_keys()
        return tuple(
            body.with_api_keys(tuple(keys.get(body.id, ())))
            for body in bodies
        )

    def get(self, profile_id: str) -> ModelConfig | None:
        for profile in self.load():
            if profile.id == profile_id:
                return profile
        return None

    def create(self, profile: ModelConfig) -> ModelConfig:
        bodies = self._load_bodies()
        if any(p.id == profile.id for p in bodies):
            raise ValueError(f"Profile id already exists: {profile.id!r}")
        bodies = (*bodies, replace(profile, api_keys=()))
        self._save_bodies(bodies)
        if profile.api_keys:
            self._set_keys(profile.id, profile.api_keys)
        return self._compose(profile.id)

    def update(
        self, profile_id: str, patch: Mapping[str, object]
    ) -> ModelConfig:
        if "api_keys" in patch:
            raise ValueError(
                "api_keys may not be set via update(); use set_api_keys()."
            )
        bodies = self._load_bodies()
        index = self._find_index(bodies, profile_id)
        current = bodies[index]
        valid_fields = {f.name for f in current.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(patch) - valid_fields
        if unknown:
            raise ValueError(f"Unknown profile field(s): {sorted(unknown)!r}")
        updated = replace(current, **dict(patch))  # type: ignore[arg-type]
        bodies = (*bodies[:index], updated, *bodies[index + 1 :])
        self._save_bodies(bodies)
        return self._compose(profile_id)

    def delete(self, profile_id: str) -> None:
        bodies = self._load_bodies()
        if not any(p.id == profile_id for p in bodies):
            raise KeyError(profile_id)
        bodies = tuple(p for p in bodies if p.id != profile_id)
        self._save_bodies(bodies)
        keys = self._load_keys()
        if profile_id in keys:
            keys.pop(profile_id)
            self._save_keys(keys)

    def set_api_keys(self, profile_id: str, keys: Sequence[str]) -> ModelConfig:
        bodies = self._load_bodies()
        if not any(p.id == profile_id for p in bodies):
            raise KeyError(profile_id)
        self._set_keys(profile_id, tuple(keys))
        return self._compose(profile_id)

    def api_key_status(self, profile_id: str) -> ApiKeyStatus:
        keys = self._load_keys().get(profile_id, ())
        if keys:
            return "present"
        # Future: detect TRANSORIA_<ID>_API_KEY env vars and return "from_env".
        return "missing"

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _compose(self, profile_id: str) -> ModelConfig:
        bodies = self._load_bodies()
        index = self._find_index(bodies, profile_id)
        keys = self._load_keys().get(profile_id, ())
        return bodies[index].with_api_keys(tuple(keys))

    @staticmethod
    def _find_index(
        bodies: Sequence[ModelConfig], profile_id: str
    ) -> int:
        for i, profile in enumerate(bodies):
            if profile.id == profile_id:
                return i
        raise KeyError(profile_id)

    def _load_bodies(self) -> tuple[ModelConfig, ...]:
        # Architecture § 3.4 — Step G: fresh installs produce no
        # seeded profiles. Users walk through the ``+ Add API
        # Profile`` modal to create their first profile from a
        # template. Existing user files (with or without the legacy
        # seeded ids) are preserved on upgrade.
        if not self.profiles_path.exists():
            return ()
        try:
            raw = self.profiles_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Cannot read profiles file: {self.profiles_path}") from exc
        if not raw.strip():
            return ()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Profiles file is not valid JSON: {self.profiles_path}"
            ) from exc
        if not isinstance(payload, list):
            raise ValueError(
                f"Profiles file must contain a JSON array: {self.profiles_path}"
            )
        return tuple(ModelConfig.from_dict(item) for item in payload)

    def _save_bodies(self, bodies: Sequence[ModelConfig]) -> None:
        self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {**body.to_dict(), "api_keys": []}  # never persist keys here
            for body in bodies
        ]
        _atomic_write_json(self.profiles_path, payload)

    def _load_keys(self) -> dict[str, tuple[str, ...]]:
        if not self.keys_path.exists():
            return {}
        try:
            raw = self.keys_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Cannot read keys file: {self.keys_path}") from exc
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Keys file is not valid JSON: {self.keys_path}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"Keys file must contain a JSON object: {self.keys_path}"
            )
        result: dict[str, tuple[str, ...]] = {}
        for profile_id, keys in payload.items():
            if isinstance(keys, list):
                result[str(profile_id)] = tuple(str(k) for k in keys)
        return result

    def _save_keys(self, keys: Mapping[str, Sequence[str]]) -> None:
        self.keys_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {pid: list(values) for pid, values in keys.items()}
        _atomic_write_json(self.keys_path, payload)

    def _set_keys(self, profile_id: str, keys: Sequence[str]) -> None:
        existing = self._load_keys()
        if keys:
            existing[profile_id] = tuple(keys)
        else:
            existing.pop(profile_id, None)
        self._save_keys(existing)


def _atomic_write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def mask_api_keys(keys: Sequence[str]) -> str:
    """Render the most recently-set key as `…XYZ` for the frontend.

    Returns an empty string when no keys are set; the API key status
    field separately exposes ``missing`` / ``present`` so the UI can
    branch without parsing this string.
    """

    if not keys:
        return ""
    last = keys[-1]
    tail = last[-4:] if len(last) > 4 else last
    return f"…{tail}"


__all__ = [
    "ApiKeyStatus",
    "DEFAULT_PROFILE_IDS",
    "ModelProfileStore",
    "mask_api_keys",
]
