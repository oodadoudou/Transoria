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


def _detect_glossary_format(path: Path) -> str | None:
    """Return ``"xlsx"`` / ``"json"`` based on file content, not extension.

    XLSX files are zip archives starting with ``PK\\x03\\x04`` (or
    ``PK\\x05\\x06`` for empty zips, ``PK\\x07\\x08`` for spanned zips
    — both rare for Excel files). JSON files start with ``{`` or ``[``
    after optional UTF-8 BOM and whitespace. Anything else returns
    ``None`` so the caller can reject the file with a typed error.

    Falls back to suffix detection if the file cannot be read (e.g.
    permission error) — tests and dev runs that mock the dialog
    provider may pass paths to files that don't exist on disk.
    """

    suffix = path.suffix.lower().lstrip(".")
    if suffix not in {"xlsx", "json"}:
        return None
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return suffix if suffix in {"xlsx", "json"} else None
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "xlsx"
    # Strip BOM + leading whitespace for JSON detection.
    stripped = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return "json"
    return None


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
        # Detect format by reading the leading bytes — a renamed CSV
        # masquerading as ``data.xlsx`` is rejected as ``None`` so the
        # caller can show a typed error instead of failing later in
        # the import path.
        fmt = _detect_glossary_format(Path(path))
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
