"""Shared bridge handler helpers."""

from __future__ import annotations

from typing import Mapping

from transoria.bridge.errors import BridgeError


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


__all__ = ["expect_string"]
