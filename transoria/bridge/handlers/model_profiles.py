"""``model_profiles.*`` bridge handlers.

Profiles persist via :class:`ModelProfileStore`. API keys live in a
separate file and never appear in the profile body returned to the
frontend — only ``api_key_status`` and a masked tail. ``test_connection``
and ``fetch_model_list`` are reserved for the LLM client integration in
a follow-up; for v1 they return ``bridge.invalid_argument`` with
``details.reason = "unsupported"`` so the UI can render a clear "not
yet wired" state instead of a runtime crash.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.llm.config import ModelConfig, ProviderFormat, ThinkingLevel
from transoria.model_profiles import ModelProfileStore, mask_api_keys
from transoria.settings import SettingsStore

ACTIVE_FIELD_BY_MODULE = {
    "translation": "active_translation_model_id",
    "glossary": "active_glossary_model_id",
}


def _profile_to_dict(profile: ModelConfig, status: str) -> dict[str, object]:
    body = profile.to_dict()
    body.pop("api_keys", None)
    body["api_key_status"] = status
    body["api_key_masked"] = mask_api_keys(profile.api_keys)
    return body


def _build_handlers(
    profile_store: ModelProfileStore,
    settings_store: SettingsStore,
) -> dict[str, object]:
    def list_profiles(_payload: Mapping[str, object]) -> dict[str, object]:
        profiles = profile_store.load()
        return {
            "profiles": [
                _profile_to_dict(p, profile_store.api_key_status(p.id))
                for p in profiles
            ]
        }

    def create(payload: Mapping[str, object]) -> dict[str, object]:
        body = payload.get("profile")
        if not isinstance(body, Mapping):
            raise BridgeError.invalid_argument(
                "profile object is required.",
                field="profile",
            )
        try:
            profile = _profile_from_payload(body)
        except (KeyError, ValueError) as exc:
            raise BridgeError.invalid_argument(
                str(exc),
                field=_field_from_exc(exc),
            ) from exc
        try:
            stored = profile_store.create(profile)
        except ValueError as exc:
            raise BridgeError.conflict(str(exc)) from exc
        return {
            "profile": _profile_to_dict(
                stored, profile_store.api_key_status(stored.id)
            )
        }

    def update(payload: Mapping[str, object]) -> dict[str, object]:
        profile_id = expect_string(payload, "id")
        patch = payload.get("patch")
        if not isinstance(patch, Mapping):
            raise BridgeError.invalid_argument(
                "patch object is required.",
                field="patch",
            )
        coerced = _coerce_patch(patch)
        try:
            stored = profile_store.update(profile_id, coerced)
        except KeyError as exc:
            raise BridgeError.not_found(
                f"profile {profile_id!r} does not exist."
            ) from exc
        except ValueError as exc:
            raise BridgeError.invalid_argument(
                str(exc),
                field=_field_from_exc(exc),
            ) from exc
        return {
            "profile": _profile_to_dict(
                stored, profile_store.api_key_status(stored.id)
            )
        }

    def delete(payload: Mapping[str, object]) -> dict[str, object]:
        profile_id = expect_string(payload, "id")
        try:
            profile_store.delete(profile_id)
        except KeyError as exc:
            raise BridgeError.not_found(
                f"profile {profile_id!r} does not exist."
            ) from exc
        # Clear active-model selections that referenced the deleted profile.
        current = settings_store.load_all()
        patch: dict[str, object] = {}
        if current.app.active_translation_model_id == profile_id:
            patch["active_translation_model_id"] = None
        if current.app.active_glossary_model_id == profile_id:
            patch["active_glossary_model_id"] = None
        if patch:
            settings_store.save_partial("app", patch)
        return {}

    def set_api_key(payload: Mapping[str, object]) -> dict[str, object]:
        profile_id = expect_string(payload, "id")
        keys = payload.get("api_keys")
        if not isinstance(keys, list):
            raise BridgeError.invalid_argument(
                "api_keys must be a list of strings.",
                field="api_keys",
            )
        try:
            stored = profile_store.set_api_keys(
                profile_id, [str(k) for k in keys]
            )
        except KeyError as exc:
            raise BridgeError.not_found(
                f"profile {profile_id!r} does not exist."
            ) from exc
        return {
            "profile": _profile_to_dict(
                stored, profile_store.api_key_status(stored.id)
            )
        }

    def select_active(payload: Mapping[str, object]) -> dict[str, object]:
        module = payload.get("module")
        if module not in ACTIVE_FIELD_BY_MODULE:
            raise BridgeError.invalid_argument(
                "module must be 'translation' or 'glossary'.",
                field="module",
            )
        profile_id = payload.get("profile_id")
        if profile_id is not None and not isinstance(profile_id, str):
            raise BridgeError.invalid_argument(
                "profile_id must be a string or null.",
                field="profile_id",
            )
        if isinstance(profile_id, str) and profile_id:
            if profile_store.get(profile_id) is None:
                raise BridgeError.not_found(
                    f"profile {profile_id!r} does not exist."
                )
        field = ACTIVE_FIELD_BY_MODULE[module]
        updated = settings_store.save_partial("app", {field: profile_id})
        return {"app": _app_settings_dict(updated.app)}

    def test_connection(_payload: Mapping[str, object]) -> dict[str, object]:
        raise BridgeError.invalid_argument(
            "test_connection is not yet implemented in this build.",
            details={"reason": "unsupported"},
        )

    def fetch_model_list(_payload: Mapping[str, object]) -> dict[str, object]:
        raise BridgeError.invalid_argument(
            "fetch_model_list is not yet implemented in this build.",
            details={"reason": "unsupported"},
        )

    return {
        "model_profiles.list": list_profiles,
        "model_profiles.create": create,
        "model_profiles.update": update,
        "model_profiles.delete": delete,
        "model_profiles.set_api_key": set_api_key,
        "model_profiles.select_active": select_active,
        "model_profiles.test_connection": test_connection,
        "model_profiles.fetch_model_list": fetch_model_list,
    }


def _profile_from_payload(body: Mapping[str, object]) -> ModelConfig:
    """Build a fresh ModelConfig for create.

    The id is generated by us if missing so the frontend doesn't have to
    guess; api_keys come in as plain strings on the optional ``api_keys``
    field.
    """

    payload = dict(body)
    payload.setdefault("id", _generate_id(payload))
    api_keys = payload.pop("api_keys", ())
    config = ModelConfig.from_dict(payload)
    if isinstance(api_keys, list):
        config = config.with_api_keys(tuple(str(k) for k in api_keys))
    return config


def _generate_id(body: Mapping[str, object]) -> str:
    seed = str(body.get("display_name") or body.get("model_id") or "profile")
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in seed).strip("-")
    if not slug:
        slug = "profile"
    import secrets  # noqa: PLC0415

    return f"{slug}-{secrets.token_hex(3)}"


def _coerce_patch(patch: Mapping[str, object]) -> dict[str, object]:
    """Coerce string-encoded enum values when the frontend sends them.

    JSON gives us the same shape as ``ModelConfig.from_dict`` expects,
    except enum strings need wrapping back into the dataclass enum types
    so ``replace`` works.
    """

    coerced: dict[str, object] = {}
    for key, value in patch.items():
        if key == "provider_format" and isinstance(value, str):
            coerced[key] = ProviderFormat(value)
        elif key == "thinking_level" and isinstance(value, str):
            coerced[key] = ThinkingLevel(value)
        elif key == "custom_headers" and isinstance(value, list):
            coerced[key] = tuple(
                (str(pair[0]), str(pair[1]))
                for pair in value
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            )
        else:
            coerced[key] = value
    return coerced


def _field_from_exc(exc: Exception) -> str | None:
    message = str(exc)
    if "Unknown profile field" in message and "'" in message:
        tail = message.split("'", 1)[1]
        if "'" in tail:
            return tail.split("'", 1)[0]
    return None


def _app_settings_dict(value: object) -> dict[str, object]:
    from dataclasses import asdict  # noqa: PLC0415

    return asdict(value)  # type: ignore[arg-type]


def register(
    router: BridgeRouter,
    *,
    profile_store: ModelProfileStore,
    settings_store: SettingsStore,
) -> None:
    for method, handler in _build_handlers(profile_store, settings_store).items():
        router.register(method, handler)  # type: ignore[arg-type]


__all__ = ["register"]
