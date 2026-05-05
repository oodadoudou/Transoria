"""``settings.*`` bridge handlers.

The handlers wrap a :class:`SettingsStore` instance. The default factory
points it at ``<cache_root>/settings.json``; tests and integration shells
can pass a different path via :func:`register`.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.router import BridgeRouter
from transoria.settings import SettingsStore
from transoria.settings.defaults import SettingsModule

VALID_MODULES: tuple[SettingsModule, ...] = (
    "app",
    "translation",
    "glossary",
    "glossary_review",
    "replacement",
)


def _expect_module(payload: Mapping[str, object]) -> SettingsModule:
    module = payload.get("module")
    if module not in VALID_MODULES:
        raise BridgeError.invalid_argument(
            f"module must be one of {VALID_MODULES!r}; got {module!r}",
            field="module",
        )
    return module  # type: ignore[return-value]


def _expect_patch(payload: Mapping[str, object]) -> Mapping[str, object]:
    patch = payload.get("patch")
    if not isinstance(patch, Mapping):
        raise BridgeError.invalid_argument(
            "patch must be a JSON object.",
            field="patch",
        )
    return patch


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_handlers(store: SettingsStore) -> dict[str, object]:
    def load_all(_payload: Mapping[str, object]) -> dict[str, object]:
        try:
            return store.load_all().to_dict()
        except OSError as exc:
            raise _settings_io_error(store, "read", exc) from exc

    def save_partial(payload: Mapping[str, object]) -> dict[str, object]:
        module = _expect_module(payload)
        patch = _expect_patch(payload)
        try:
            _, rejected = store.save_partial_lenient(module, patch)
        except ValueError as exc:
            field = _extract_field(exc)
            raise BridgeError.invalid_argument(str(exc), field=field) from exc
        except OSError as exc:
            raise _settings_io_error(store, "save", exc) from exc
        return {
            "saved_at": _utc_now_iso(),
            # Per-field rejection list. Empty when every patch field
            # accepted its value. The frontend can surface these as
            # warnings while still confirming the rest of the save.
            "rejected_fields": rejected,
        }

    def reset_module(payload: Mapping[str, object]) -> dict[str, object]:
        module = _expect_module(payload)
        try:
            defaults = store.reset_module(module)
        except OSError as exc:
            raise _settings_io_error(store, "reset", exc) from exc
        return asdict(defaults)

    return {
        "settings.load_all": load_all,
        "settings.save_partial": save_partial,
        "settings.reset_module": reset_module,
    }


def _extract_field(exc: ValueError) -> str | None:
    """Best-effort extraction of the offending field name from a ValueError.

    The store raises ``ValueError("Unknown settings field: 'foo'")`` and
    ``ValueError("Field 'foo' expects ...")``. Pulling the quoted name out
    keeps the bridge response useful without a custom exception hierarchy.
    """

    message = str(exc)
    for marker in ("settings field: ", "Field "):
        if marker in message:
            tail = message.split(marker, 1)[1]
            if tail.startswith("'") and "'" in tail[1:]:
                return tail[1 : 1 + tail[1:].index("'")]
    return None


def _settings_io_error(store: SettingsStore, action: str, exc: OSError) -> BridgeError:
    settings_path = str(getattr(store, "path", "settings.json"))
    return BridgeError(
        "bridge.io_error",
        (
            f"Cannot {action} settings file: {settings_path}. "
            f"{exc}. On Windows, make sure Transoria is extracted to a normal "
            "writable folder and not running from Program Files or a read-only ZIP."
        ),
        retryable=True,
        details={
            "settings_path": settings_path,
            "operation": action,
            "error": str(exc),
        },
    )


def register(router: BridgeRouter, *, store: SettingsStore) -> None:
    """Register settings handlers using the supplied store."""

    for method, handler in _build_handlers(store).items():
        router.register(method, handler)  # type: ignore[arg-type]


def default_store(cache_root: Path) -> SettingsStore:
    """Return the production store rooted at ``cache_root``."""

    return SettingsStore(path=cache_root / "settings.json")


__all__ = ["default_store", "register"]
