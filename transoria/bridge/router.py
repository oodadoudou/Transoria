"""Method-name dispatcher for the bridge.

The router is intentionally framework-agnostic: pywebview, an HTTP server,
or a test harness can all wrap the same router. Handlers are plain
callables of shape ``(payload: Mapping[str, Any]) -> Mapping[str, Any]``.
"""

from __future__ import annotations

from typing import Callable, Mapping, MutableMapping

from transoria.bridge.errors import BridgeError, BridgeErrorPayload

BridgeHandler = Callable[[Mapping[str, object]], Mapping[str, object]]


class BridgeRouter:
    """Registry that maps method names (``domain.method``) to handlers."""

    def __init__(self) -> None:
        self._handlers: MutableMapping[str, BridgeHandler] = {}

    def register(self, method: str, handler: BridgeHandler) -> None:
        if method in self._handlers:
            raise ValueError(f"Handler already registered for {method!r}")
        self._handlers[method] = handler

    def methods(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def dispatch(
        self, method: str, payload: Mapping[str, object] | None = None
    ) -> Mapping[str, object]:
        """Run the handler for ``method`` and return its response.

        Raises :class:`BridgeError` for handler-level failures so callers can
        translate them into the contract's error envelope. Unknown methods
        raise ``bridge.not_found``.
        """

        handler = self._handlers.get(method)
        if handler is None:
            raise BridgeError.not_found(
                f"Bridge method not registered: {method!r}",
                details={"method": method},
            )
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise BridgeError.invalid_argument(
                "Payload must be a JSON object.",
                details={"method": method},
            )
        return handler(payload)

    def call(
        self, method: str, payload: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Dispatch and serialize errors as bridge envelopes.

        Returns the handler response untouched on success. On failure it
        raises ``BridgeError`` (for known errors) or wraps unexpected
        exceptions in ``bridge.io_error`` so the UI still receives a typed
        payload.
        """

        try:
            response = self.dispatch(method, payload)
        except BridgeError:
            raise
        except Exception as exc:  # noqa: BLE001 - intentional catch-all
            raise BridgeError(
                "bridge.io_error",
                f"Unhandled error in {method}: {exc}",
                retryable=False,
                details={"method": method, "exception": type(exc).__name__},
            ) from exc
        if not isinstance(response, Mapping):
            raise BridgeError(
                "bridge.io_error",
                f"Handler {method} returned a non-mapping response.",
                retryable=False,
                details={"method": method},
            )
        return dict(response)


def build_default_router(*, cache_root: "Path | None" = None) -> BridgeRouter:
    """Wire the production handler set.

    New domains register themselves here so a fresh router instance always
    matches the contract surface. ``cache_root`` lets tests inject a tmp
    path; production callers omit it and the default project-relative
    ``.transoria-cache`` directory is used.
    """

    from pathlib import Path  # noqa: PLC0415 — local to keep top-level small

    from transoria.bridge.handlers.app import register as register_app
    from transoria.bridge.handlers.app import _read_app_version  # noqa: PLC0415
    from transoria.bridge.handlers.dialogs import (  # noqa: PLC0415
        NullDialogProvider,
        register as register_dialogs,
    )
    from transoria.bridge.handlers.model_profiles import (  # noqa: PLC0415
        register as register_model_profiles,
    )
    from transoria.bridge.handlers.prompts import (  # noqa: PLC0415
        register as register_prompts,
    )
    from transoria.bridge.handlers.replacement import (  # noqa: PLC0415
        register as register_replacement,
    )
    from transoria.bridge.handlers.settings import (  # noqa: PLC0415
        default_store,
        register as register_settings,
    )
    from transoria.bridge.handlers.tasks import register as register_tasks
    from transoria.bridge.handlers.updates import (  # noqa: PLC0415
        NullUpdateChecker,
        register as register_updates,
    )
    from transoria.model_profiles import ModelProfileStore  # noqa: PLC0415

    if cache_root is None:
        cache_root = Path(__file__).resolve().parents[2] / ".transoria-cache"

    settings_store = default_store(cache_root)
    profile_store = ModelProfileStore.from_cache_root(cache_root)
    current_version = _read_app_version()

    router = BridgeRouter()
    register_app(router)
    register_tasks(router)
    register_settings(router, store=settings_store)
    register_model_profiles(
        router,
        profile_store=profile_store,
        settings_store=settings_store,
    )
    register_prompts(
        router,
        cache_root=cache_root,
        settings_store=settings_store,
    )
    register_dialogs(router, provider=NullDialogProvider())
    register_replacement(router)
    register_updates(
        router,
        checker=NullUpdateChecker(current_version=current_version),
        current_version=current_version,
    )
    return router


__all__ = ["BridgeError", "BridgeErrorPayload", "BridgeHandler", "BridgeRouter", "build_default_router"]
