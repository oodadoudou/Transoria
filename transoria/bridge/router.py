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


def build_default_router(
    *,
    cache_root: "Path | None" = None,
    dialog_provider: "object | None" = None,
    update_checker: "object | None" = None,
    llm_client_factory: "Callable[[], object] | None" = None,
) -> BridgeRouter:
    """Wire the production handler set.

    New domains register themselves here so a fresh router instance always
    matches the contract surface. ``cache_root`` lets tests inject a tmp
    path; production callers omit it and the default project-relative
    ``.transoria-cache`` directory is used. ``dialog_provider`` overrides
    the default :class:`NullDialogProvider` (pass the pywebview adapter from
    ``app.py``). ``update_checker`` lets app launchers choose a real network
    checker while tests/dev harnesses can stay deterministic. ``llm_client_factory``
    overrides the production httpx-backed LLM client; tests inject a fake
    transport here.
    """

    from transoria.app_paths import default_cache_root  # noqa: PLC0415
    from transoria.bridge.handlers.app import register as register_app
    from transoria.bridge.handlers.app import _read_app_version  # noqa: PLC0415
    from transoria.bridge.handlers.dialogs import (  # noqa: PLC0415
        NullDialogProvider,
        register as register_dialogs,
    )
    from transoria.bridge.handlers.model_profiles import (  # noqa: PLC0415
        register as register_model_profiles,
    )
    from transoria.bridge.handlers.model_templates import (  # noqa: PLC0415
        register as register_model_templates,
    )
    from transoria.bridge.handlers.prompts import (  # noqa: PLC0415
        register as register_prompts,
    )
    from transoria.bridge.handlers.glossary_imports import (  # noqa: PLC0415
        register as register_glossary_imports,
    )
    from transoria.bridge.handlers.replacement import (  # noqa: PLC0415
        register_parsers as register_replacement_parsers,
        register_tasks as register_replacement_tasks,
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
    from transoria.bridge.task_registry import TaskRegistry  # noqa: PLC0415
    from transoria.bridge.task_service import (  # noqa: PLC0415
        TaskService,
        default_llm_client_factory,
        make_llm_client_factory,
    )
    from transoria.model_profiles import ModelProfileStore  # noqa: PLC0415
    from transoria.runtime.cache import TaskCache  # noqa: PLC0415

    if cache_root is None:
        cache_root = default_cache_root()

    settings_store = default_store(cache_root)
    profile_store = ModelProfileStore.from_cache_root(cache_root)
    task_cache = TaskCache(root=cache_root / "tasks")
    task_registry = TaskRegistry()
    # Honor the user's ``app.proxy_url`` setting on every LLM call.
    # Tests pass an explicit ``llm_client_factory`` and bypass the proxy
    # plumbing — production goes through ``make_llm_client_factory`` so
    # changing the proxy in App Settings affects the very next call
    # without a restart.
    proxy_aware_factory = (
        llm_client_factory
        if llm_client_factory is not None
        else make_llm_client_factory(settings_store)
    )
    task_service = TaskService(
        cache=task_cache,
        registry=task_registry,
        settings_store=settings_store,
        profile_store=profile_store,
        prompts_cache_root=cache_root,
        llm_client_factory=proxy_aware_factory,
    )
    current_version = _read_app_version()

    router = BridgeRouter()
    register_app(router)
    register_tasks(router, service=task_service)
    register_settings(router, store=settings_store)
    register_model_profiles(
        router,
        profile_store=profile_store,
        settings_store=settings_store,
    )
    register_model_templates(router)
    register_prompts(
        router,
        cache_root=cache_root,
        settings_store=settings_store,
        profile_store=profile_store,
    )
    def _allowed_dialog_roots() -> list[Path]:
        # Restrict open_directory / reveal_file to the user's
        # configured output folders so the bridge can't be coerced
        # into shelling out arbitrary host paths from the renderer.
        try:
            settings = settings_store.load_all()
        except Exception:  # noqa: BLE001
            return []
        roots: list[Path] = []
        for folder in (
            settings.translation.output_folder,
            settings.glossary.output_folder,
            settings.replacement.output_folder,
        ):
            if folder:
                roots.append(Path(folder))
        return roots

    register_dialogs(
        router,
        provider=dialog_provider or NullDialogProvider(),
        allowed_roots_provider=_allowed_dialog_roots,
    )
    register_replacement_parsers(router)
    register_replacement_tasks(router, service=task_service)
    register_glossary_imports(router, cache_root=cache_root)
    # Bind the user's ``app.proxy_url`` to the supplied checker so its
    # outbound HTTP calls (release list + asset download) honor the
    # same proxy as LLM calls. Done with ``setattr`` (no static type
    # check) because ``update_checker`` is typed as ``object`` here —
    # this avoids forcing every dev-harness checker to grow the
    # field.
    if update_checker is not None and hasattr(update_checker, "proxy_provider"):
        try:
            setattr(
                update_checker,
                "proxy_provider",
                lambda: settings_store.load_all().app.proxy_url or None,
            )
        except Exception:  # noqa: BLE001
            pass
    register_updates(
        router,
        checker=update_checker or NullUpdateChecker(current_version=current_version),
        current_version=current_version,
    )
    return router


__all__ = [
    "BridgeError",
    "BridgeErrorPayload",
    "BridgeHandler",
    "BridgeRouter",
    "build_default_router",
]
