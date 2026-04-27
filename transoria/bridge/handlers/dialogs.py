"""``dialogs.*`` bridge handlers.

The desktop shell (pywebview) supplies the actual native dialog runner;
the bridge handler is a thin adapter so the frontend never imports a
host-specific module. For automated tests and the dev fallback, callers
inject a :class:`DialogProvider` that returns canned paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter


class DialogProvider(Protocol):
    def choose_directory(self, *, initial_path: str | None) -> str | None: ...

    def choose_file(
        self, *, initial_path: str | None, extensions: tuple[str, ...]
    ) -> str | None: ...

    def open_directory(self, path: str) -> None: ...

    def reveal_file(self, path: str) -> None: ...


@dataclass(frozen=True)
class NullDialogProvider:
    """Default provider used in tests and headless dev runs.

    Every dialog call returns ``None`` (user cancelled) and OS actions
    raise ``bridge.io_error`` so callers see a typed failure instead of a
    hung native call.
    """

    def choose_directory(self, *, initial_path: str | None = None) -> str | None:
        return None

    def choose_file(
        self,
        *,
        initial_path: str | None = None,
        extensions: tuple[str, ...] = (),
    ) -> str | None:
        return None

    def open_directory(self, path: str) -> None:
        raise BridgeError(
            "bridge.io_error",
            f"open_directory is unavailable in this build (path={path}).",
            retryable=False,
            details={"path": path},
        )

    def reveal_file(self, path: str) -> None:
        raise BridgeError(
            "bridge.io_error",
            f"reveal_file is unavailable in this build (path={path}).",
            retryable=False,
            details={"path": path},
        )


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BridgeError.invalid_argument(
            f"{key} must be a string when provided.",
            field=key,
        )
    return value


def _build_handlers(provider: DialogProvider) -> dict[str, Callable[[Mapping[str, object]], dict[str, object]]]:
    def choose_input_directory(payload: Mapping[str, object]) -> dict[str, object]:
        path = provider.choose_directory(
            initial_path=_optional_string(payload, "initial_path")
        )
        return {"path": path}

    def choose_output_directory(payload: Mapping[str, object]) -> dict[str, object]:
        path = provider.choose_directory(
            initial_path=_optional_string(payload, "initial_path")
        )
        return {"path": path}

    def choose_glossary_file(payload: Mapping[str, object]) -> dict[str, object]:
        allow_xlsx = bool(payload.get("allow_xlsx", True))
        allow_json = bool(payload.get("allow_json", True))
        extensions: list[str] = []
        if allow_xlsx:
            extensions.append("xlsx")
        if allow_json:
            extensions.append("json")
        path = provider.choose_file(
            initial_path=_optional_string(payload, "initial_path"),
            extensions=tuple(extensions),
        )
        if path is None:
            return {"path": None, "format": None}
        suffix = Path(path).suffix.lower().lstrip(".")
        fmt = suffix if suffix in {"xlsx", "json"} else None
        return {"path": path, "format": fmt}

    def choose_replacement_rules_file(
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        path = provider.choose_file(
            initial_path=_optional_string(payload, "initial_path"),
            extensions=("txt",),
        )
        return {"path": path}

    def open_directory(payload: Mapping[str, object]) -> dict[str, object]:
        path = expect_string(payload, "path")
        if not Path(path).exists():
            raise BridgeError.not_found(
                f"path does not exist: {path!r}",
                details={"path": path},
            )
        provider.open_directory(path)
        return {}

    def reveal_file(payload: Mapping[str, object]) -> dict[str, object]:
        path = expect_string(payload, "path")
        if not Path(path).exists():
            raise BridgeError.not_found(
                f"path does not exist: {path!r}",
                details={"path": path},
            )
        provider.reveal_file(path)
        return {}

    return {
        "dialogs.choose_input_directory": choose_input_directory,
        "dialogs.choose_output_directory": choose_output_directory,
        "dialogs.choose_glossary_file": choose_glossary_file,
        "dialogs.choose_replacement_rules_file": choose_replacement_rules_file,
        "dialogs.open_directory": open_directory,
        "dialogs.reveal_file": reveal_file,
    }


def register(router: BridgeRouter, *, provider: DialogProvider) -> None:
    for method, handler in _build_handlers(provider).items():
        router.register(method, handler)  # type: ignore[arg-type]


__all__ = ["DialogProvider", "NullDialogProvider", "register"]
