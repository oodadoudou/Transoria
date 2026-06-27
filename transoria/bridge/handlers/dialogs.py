"""``dialogs.*`` bridge handlers.

The desktop shell (pywebview) supplies the actual native dialog runner;
the bridge handler is a thin adapter so the frontend never imports a
host-specific module. For automated tests and the dev fallback, callers
inject a :class:`DialogProvider` that returns canned paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter


class DialogProvider(Protocol):
    def choose_directory(self, *, initial_path: str | None) -> str | None: ...

    def choose_file(
        self, *, initial_path: str | None, extensions: tuple[str, ...]
    ) -> str | None: ...

    def save_file(
        self,
        *,
        default_filename: str,
        extensions: tuple[str, ...],
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

    def save_file(
        self,
        *,
        default_filename: str = "",
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


AllowedRootsProvider = Callable[[], "Sequence[Path]"]


def _validated_dialog_path(
    raw: str, allowed_roots_provider: AllowedRootsProvider | None
) -> Path:
    """Resolve a JS-supplied path and reject anything outside the
    user-configured output folders.

    Without this check, a compromised renderer could call
    ``dialogs.open_directory("/Applications/Calculator.app")`` (macOS)
    or any other path the host process can read, turning the bridge
    into a generic shell-out. ``allowed_roots_provider`` returns the
    current Translation / Glossary / Replacement output folders; an
    empty list disables the check (for early dev / smoke tools that
    haven't configured any output yet).
    """

    target = Path(raw)
    if not target.exists():
        raise BridgeError.not_found(
            f"path does not exist: {raw!r}",
            details={"path": raw},
        )
    resolved = target.resolve(strict=False)
    if allowed_roots_provider is None:
        return resolved
    roots = [r for r in allowed_roots_provider() if r]
    if not roots:
        return resolved
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=False))
            return resolved
        except ValueError:
            continue
    raise BridgeError.invalid_argument(
        "path must be inside the configured output folder.",
        field="path",
        details={
            "path": raw,
            "allowed_roots": [str(r) for r in roots],
        },
    )


def _build_handlers(
    provider: DialogProvider,
    *,
    allowed_roots_provider: AllowedRootsProvider | None,
) -> dict[str, Callable[[Mapping[str, object]], dict[str, object]]]:
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
            extensions=(),
        )
        return {"path": path}

    def choose_save_path(payload: Mapping[str, object]) -> dict[str, object]:
        default_filename = (
            _optional_string(payload, "default_filename") or ""
        )
        raw_exts = payload.get("extensions", ())
        extensions: tuple[str, ...] = (
            tuple(str(ext) for ext in raw_exts)
            if isinstance(raw_exts, (list, tuple))
            else ()
        )
        path = provider.save_file(
            default_filename=default_filename,
            extensions=extensions,
        )
        return {"path": path}

    def open_directory(payload: Mapping[str, object]) -> dict[str, object]:
        path = _validated_dialog_path(
            expect_string(payload, "path"), allowed_roots_provider
        )
        provider.open_directory(str(path))
        return {}

    def reveal_file(payload: Mapping[str, object]) -> dict[str, object]:
        path = _validated_dialog_path(
            expect_string(payload, "path"), allowed_roots_provider
        )
        provider.reveal_file(str(path))
        return {}

    return {
        "dialogs.choose_input_directory": choose_input_directory,
        "dialogs.choose_output_directory": choose_output_directory,
        "dialogs.choose_glossary_file": choose_glossary_file,
        "dialogs.choose_replacement_rules_file": choose_replacement_rules_file,
        "dialogs.choose_save_path": choose_save_path,
        "dialogs.open_directory": open_directory,
        "dialogs.reveal_file": reveal_file,
    }


def register(
    router: BridgeRouter,
    *,
    provider: DialogProvider,
    allowed_roots_provider: AllowedRootsProvider | None = None,
) -> None:
    for method, handler in _build_handlers(
        provider, allowed_roots_provider=allowed_roots_provider
    ).items():
        router.register(method, handler)  # type: ignore[arg-type]


__all__ = ["DialogProvider", "NullDialogProvider", "register"]
