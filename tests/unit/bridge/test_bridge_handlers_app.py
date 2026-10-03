"""Tests for ``transoria.bridge.handlers.app``."""

from __future__ import annotations

import importlib.metadata
import platform
import sys
import tomllib
from pathlib import Path

import pytest

from transoria.bridge import build_default_router
from transoria.bridge.handlers import app


def test_app_get_metadata_shape_matches_contract():
    router = build_default_router()

    response = router.call("app.get_metadata", {})

    assert set(response) == {
        "app_version",
        "platform",
        "build_mode",
        "python_version",
        "cache_root",
    }


def test_app_get_metadata_reports_runtime_values():
    router = build_default_router()

    response = router.call("app.get_metadata", {})

    expected_platform = {
        "darwin": "darwin",
        "win32": "win32",
        "linux": "linux",
    }.get(sys.platform, sys.platform)
    assert response["platform"] == expected_platform
    assert response["python_version"] == platform.python_version()
    assert response["build_mode"] in {"dev", "packaged"}
    assert isinstance(response["app_version"], str) and response["app_version"]
    assert isinstance(response["cache_root"], str) and response["cache_root"]


def test_app_get_metadata_is_registered_in_default_router():
    router = build_default_router()

    assert "app.get_metadata" in router.methods()


@pytest.mark.parametrize("frozen", [False, True])
def test_app_version_prefers_current_project_over_stale_install(
    tmp_path, monkeypatch, frozen
):
    monkeypatch.setattr(app, "__file__", str(tmp_path / "transoria/bridge/handlers/app.py"))
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.6.0")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.example]\nversion = "9.9.9"\n[project]\nversion = "1.6.1"\n',
        encoding="utf-8",
    )

    assert app.get_metadata({})["app_version"] == "1.6.1"


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("project", [
    None,
    "invalid TOML [",
    "[project]\nname = 'transoria'\n",
    "project = 42\n",
    "[project]\nversion = 42\n",
    "[project]\nversion = ' '\n",
])
def test_app_version_falls_back_to_installed_metadata(
    tmp_path, monkeypatch, frozen, project
):
    monkeypatch.setattr(app, "__file__", str(tmp_path / "transoria/bridge/handlers/app.py"))
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "2.3.4")
    if project is not None:
        (tmp_path / "pyproject.toml").write_text(project, encoding="utf-8")

    assert app._read_app_version() == "2.3.4"


def test_app_version_without_project_or_installed_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "__file__", str(tmp_path / "transoria/bridge/handlers/app.py"))

    def missing_package(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing_package)

    assert app._read_app_version() == "0.0.0"


def test_app_metadata_and_update_checker_report_live_project_version(tmp_path):
    project = Path(__file__).resolve().parents[3] / "pyproject.toml"
    expected = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]

    router = build_default_router(cache_root=tmp_path)
    assert router.call("app.get_metadata", {})["app_version"] == expected
    assert router.call("updates.check_latest", {})["current_version"] == expected
