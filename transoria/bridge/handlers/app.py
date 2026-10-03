"""``app.*`` bridge handlers.

Currently exposes :func:`get_metadata`, the proof-of-life RPC the frontend
calls during shell bootstrap. ``cache_root`` is read-only display only —
the contract forbids exposing project/cache location controls in module
settings.
"""

from __future__ import annotations

import platform
import sys
import tomllib
from pathlib import Path
from typing import Mapping

from transoria.app_paths import default_cache_root
from transoria.bridge.router import BridgeRouter

PLATFORM_KEYS: dict[str, str] = {
    "darwin": "darwin",
    "win32": "win32",
    "linux": "linux",
}


def _platform_key() -> str:
    raw = sys.platform
    return PLATFORM_KEYS.get(raw, raw)


def _read_app_version() -> str:
    """Prefer the running code's version over potentially stale egg-info."""

    version_from_pyproject = _read_version_from_pyproject()
    if version_from_pyproject != "0.0.0":
        return version_from_pyproject

    try:
        from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

        return version("transoria")
    except PackageNotFoundError:
        return "0.0.0"


def _read_version_from_pyproject() -> str:
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return "0.0.0"
    project = data.get("project")
    value = project.get("version") if isinstance(project, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else "0.0.0"


def _build_mode() -> str:
    return "packaged" if getattr(sys, "frozen", False) else "dev"


def _cache_root() -> str:
    """Return the runtime cache root.

    Packaged builds use the user's app-data directory; source-mode shells keep
    the project-relative cache path so development remains deterministic.
    """

    return default_cache_root().as_posix()


def get_metadata(_payload: Mapping[str, object]) -> dict[str, object]:
    return {
        "app_version": _read_app_version(),
        "platform": _platform_key(),
        "build_mode": _build_mode(),
        "python_version": platform.python_version(),
        "cache_root": _cache_root(),
    }


def register(router: BridgeRouter) -> None:
    router.register("app.get_metadata", get_metadata)


__all__ = ["get_metadata", "register"]
