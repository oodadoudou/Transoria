"""Tests for ``transoria.bridge.handlers.app``."""

from __future__ import annotations

import platform
import sys

from transoria.bridge import build_default_router


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
