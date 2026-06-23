"""Drift guards for `app.py` defaults that must match repo state.

These constants are easy to forget when the repo moves, the wrong owner is
typed, or a fork is opened. The tests here lock the constants to ground
truth and fail loudly when they desync.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import app

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_dev_bridge_port_does_not_overlap_packaged_app_default():
    assert app.PACKAGED_BRIDGE_PORT == 5018
    assert app.DEV_BRIDGE_PORT == 5019
    assert app.DEFAULT_BRIDGE_PORT == app.DEV_BRIDGE_PORT


def _origin_url() -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _parse_owner_repo(url: str) -> str | None:
    """Extract ``<owner>/<repo>`` from a GitHub HTTPS or SSH URL."""

    https_match = re.match(
        r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        url,
    )
    ssh_match = re.match(
        r"git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        url,
    )
    match = https_match or ssh_match
    if match is None:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


def test_default_update_repository_matches_git_origin():
    url = _origin_url()
    if url is None:
        pytest.skip("git origin not configured; cannot verify constant")
    parsed = _parse_owner_repo(url)
    if parsed is None:
        pytest.skip(f"unrecognized origin URL shape: {url!r}")
    assert app.DEFAULT_UPDATE_REPOSITORY == parsed, (
        "DEFAULT_UPDATE_REPOSITORY drifted from git origin: "
        f"app.py says {app.DEFAULT_UPDATE_REPOSITORY!r}, "
        f"git origin parses to {parsed!r}"
    )
