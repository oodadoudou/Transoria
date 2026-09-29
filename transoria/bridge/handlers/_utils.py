"""Shared bridge handler helpers."""

from __future__ import annotations

from typing import Callable, Mapping

from transoria.bridge.errors import BridgeError
from transoria.settings import SettingsStore
from transoria.settings.defaults import AllSettings, SettingsModule, merge_module


def save_selection(
    store: SettingsStore,
    kind: SettingsModule,
    app_patch: Mapping[str, object],
    module_patch: Mapping[str, object] | None,
    notify: Callable[[str], None] | None,
) -> AllSettings:
    current = store.load_all()
    updated = current.with_module("app", merge_module(current.app, app_patch))
    if module_patch:
        updated = updated.with_module(
            kind, merge_module(getattr(current, kind), module_patch)
        )
    store.save_all(updated)
    try:
        if notify:
            notify(kind)
    except BridgeError:
        store.save_partial("app", {key: getattr(current.app, key) for key in app_patch})
        if module_patch:
            store.save_partial(
                kind, {key: getattr(getattr(current, kind), key) for key in module_patch}
            )
        raise
    return updated


def expect_string(
    payload: Mapping[str, object], key: str, *, allow_empty: bool = False
) -> str:
    """Return ``payload[key]`` as a non-empty string.

    Used by handlers that need an id / module / preset_id, etc. Raises
    ``bridge.invalid_argument`` with ``details.field = key`` when the
    value is missing or wrongly typed.
    """

    value = payload.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise BridgeError.invalid_argument(
            f"{key} is required.",
            field=key,
        )
    return value


__all__ = ["expect_string", "save_selection"]
