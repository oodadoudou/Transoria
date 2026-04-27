"""Tests for ``transoria.bridge.handlers.updates``."""

from __future__ import annotations

from typing import Mapping

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.updates import (
    NullUpdateChecker,
    register,
)


class StubChecker:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def check_latest(
        self, *, channel: str, current_version: str
    ) -> Mapping[str, object]:
        return {
            "current_version": current_version,
            "latest_version": "1.0.0",
            "is_newer_available": True,
            "release_notes_markdown": f"channel={channel}",
            "release_url": "https://example.invalid/release",
            "published_at": "2026-04-27T00:00:00Z",
            "asset": None,
        }

    def open_release_page(self, url: str) -> None:
        self.opened.append(url)

    def download_asset(
        self, *, url: str, suggested_filename: str
    ) -> str:
        return f"/tmp/{suggested_filename}"


@pytest.fixture
def router_and_stub() -> tuple[BridgeRouter, StubChecker]:
    checker = StubChecker()
    router = BridgeRouter()
    register(router, checker=checker, current_version="0.9.0")
    return router, checker


def test_check_latest_returns_payload(router_and_stub):
    router, _ = router_and_stub

    response = router.call("updates.check_latest", {"request_id": "rid"})

    assert response["current_version"] == "0.9.0"
    assert response["latest_version"] == "1.0.0"
    assert response["release_notes_markdown"] == "channel=stable"


def test_check_latest_rejects_invalid_channel(router_and_stub):
    router, _ = router_and_stub

    with pytest.raises(BridgeError) as caught:
        router.call(
            "updates.check_latest",
            {"request_id": "rid", "channel": "alpha"},
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_open_release_page_passes_url(router_and_stub):
    router, checker = router_and_stub

    response = router.call(
        "updates.open_release_page", {"url": "https://example.invalid"}
    )

    assert response == {}
    assert checker.opened == ["https://example.invalid"]


def test_download_asset_returns_saved_path(router_and_stub):
    router, _ = router_and_stub

    response = router.call(
        "updates.download_asset",
        {
            "request_id": "rid",
            "asset_url": "https://example.invalid/file.zip",
            "suggested_filename": "file.zip",
        },
    )

    assert response == {"saved_path": "/tmp/file.zip"}


def test_null_checker_reports_up_to_date():
    router = BridgeRouter()
    register(
        router,
        checker=NullUpdateChecker(current_version="0.5.0"),
        current_version="0.5.0",
    )

    response = router.call("updates.check_latest", {"request_id": "rid"})

    assert response["is_newer_available"] is False
    assert response["asset"] is None


def test_null_checker_open_release_raises_io_error():
    router = BridgeRouter()
    register(
        router,
        checker=NullUpdateChecker(current_version="0.5.0"),
        current_version="0.5.0",
    )

    with pytest.raises(BridgeError) as caught:
        router.call(
            "updates.open_release_page", {"url": "https://example.invalid"}
        )

    assert caught.value.code == "bridge.io_error"
