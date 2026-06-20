"""Tests for the tiny pywebview native API surface in ``app.py``."""

from __future__ import annotations

import importlib
import sys


class FakeDialogProvider:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.revealed: list[str] = []

    def choose_directory(self, *, initial_path: str | None = None) -> str:
        return initial_path or "/chosen/folder"

    def choose_file(
        self, *, initial_path: str | None = None, extensions: tuple[str, ...] = ()
    ) -> str:
        suffix = extensions[0] if extensions else "txt"
        return f"{initial_path or '/chosen/file'}.{suffix}"

    def save_file(
        self,
        *,
        default_filename: str = "",
        extensions: tuple[str, ...] = (),
    ) -> str:
        suffix = extensions[0] if extensions else "txt"
        name = default_filename or "saved"
        return f"/saved/{name}.{suffix}"

    def open_directory(self, path: str) -> None:
        self.opened.append(path)

    def reveal_file(self, path: str) -> None:
        self.revealed.append(path)


def _native_api():
    if "app" not in sys.modules:
        importlib.import_module("app")
    app_module = sys.modules["app"]
    provider = FakeDialogProvider()
    return app_module._build_native_api(provider), provider


def test_native_api_exposes_only_os_helpers():
    api, _ = _native_api()

    visible = [name for name in dir(api) if not name.startswith("_")]

    assert sorted(visible) == [
        "choose_directory",
        "choose_file",
        "open_directory",
        "reveal_file",
        "save_file",
    ]


def test_choose_directory_returns_path():
    api, _ = _native_api()

    response = api.choose_directory({"initial_path": "/tmp"})

    assert response == {"path": "/tmp"}


def test_choose_file_passes_extensions():
    api, _ = _native_api()

    response = api.choose_file({"initial_path": "/tmp/rules", "extensions": ["txt"]})

    assert response == {"path": "/tmp/rules.txt"}


def test_open_and_reveal_file_delegate_to_provider():
    api, provider = _native_api()

    assert api.open_directory({"path": "/tmp/out"}) == {"ok": True}
    assert api.reveal_file({"path": "/tmp/out/file.txt"}) == {"ok": True}

    assert provider.opened == ["/tmp/out"]
    assert provider.revealed == ["/tmp/out/file.txt"]
