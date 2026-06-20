"""Tests for ``transoria.app_paths``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from transoria.app_paths import (
    PORTABLE_USER_DATA_DIR,
    default_cache_root,
)


def test_default_cache_root_uses_repo_dot_cache_when_not_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    root = default_cache_root()

    assert root.name == ".transoria-cache"


def test_default_cache_root_is_exe_sibling_on_windows_packaged(
    monkeypatch, tmp_path: Path
):
    exe = tmp_path / "Transoria.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "win32")

    root = default_cache_root()

    assert root == tmp_path / PORTABLE_USER_DATA_DIR


def test_default_cache_root_falls_back_to_localappdata_when_portable_unwritable(
    monkeypatch, tmp_path: Path
):
    exe = tmp_path / "Program Files" / "Transoria" / "Transoria.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr("transoria.app_paths._is_writable_cache_dir", lambda _: False)

    root = default_cache_root()

    assert root == local_app_data / "Transoria"


def test_default_cache_root_uses_application_support_on_macos_packaged(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    root = default_cache_root()

    assert root.parts[-3:] == ("Library", "Application Support", "Transoria")


@pytest.mark.skipif(
    sys.platform == "win32", reason="linux/xdg layout doesn't apply on Windows"
)
def test_default_cache_root_uses_xdg_on_linux_packaged(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    root = default_cache_root()

    assert root == tmp_path / "xdg" / "Transoria"
