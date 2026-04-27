"""``updates.*`` bridge handlers.

Update v1 is conservative: check, compare, show, open, download. The
network calls live behind a :class:`UpdateChecker` Protocol so tests and
dev runs can inject a stub. The default checker hits GitHub; until the
release pipeline exists it returns "no updates" so the UI can render a
clean state.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter


class UpdateChecker(Protocol):
    def check_latest(
        self, *, channel: str, current_version: str
    ) -> Mapping[str, object]: ...

    def open_release_page(self, url: str) -> None: ...

    def download_asset(
        self, *, url: str, suggested_filename: str
    ) -> str: ...


@dataclass(frozen=True)
class NullUpdateChecker:
    """Default checker for headless / dev runs.

    ``check_latest`` reports the current build as up-to-date, with no
    asset, and explicitly never raises so the App Settings page can show
    the "you're up to date" state without error noise. ``open_release_page``
    and ``download_asset`` raise typed errors so the buttons behave
    predictably until the real release pipeline lands.
    """

    current_version: str

    def check_latest(
        self, *, channel: str, current_version: str
    ) -> Mapping[str, object]:
        del channel  # not yet used; kept for stable signature
        return {
            "current_version": current_version,
            "latest_version": current_version,
            "is_newer_available": False,
            "release_notes_markdown": "",
            "release_url": "",
            "published_at": "",
            "asset": None,
        }

    def open_release_page(self, url: str) -> None:
        raise BridgeError(
            "bridge.io_error",
            "release page open is unavailable in this build.",
            retryable=False,
            details={"url": url},
        )

    def download_asset(
        self, *, url: str, suggested_filename: str
    ) -> str:
        raise BridgeError(
            "bridge.io_error",
            "asset download is unavailable in this build.",
            retryable=False,
            details={"url": url, "filename": suggested_filename},
        )


def _platform_key() -> str:
    raw = sys.platform
    return {"darwin": "darwin", "win32": "win32", "linux": "linux"}.get(raw, raw)


def _build_handlers(
    checker: UpdateChecker,
    current_version: str,
) -> dict[str, Callable[[Mapping[str, object]], dict[str, object]]]:
    def check_latest(payload: Mapping[str, object]) -> dict[str, object]:
        channel = payload.get("channel", "stable")
        if channel not in ("stable", "prerelease"):
            raise BridgeError.invalid_argument(
                "channel must be 'stable' or 'prerelease'.",
                field="channel",
            )
        try:
            return dict(
                checker.check_latest(
                    channel=str(channel),
                    current_version=current_version,
                )
            )
        except BridgeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BridgeError(
                "update.network_unavailable",
                f"update check failed: {exc}",
                retryable=True,
            ) from exc

    def open_release_page(payload: Mapping[str, object]) -> dict[str, object]:
        url = expect_string(payload, "url")
        checker.open_release_page(url)
        return {}

    def download_asset(payload: Mapping[str, object]) -> dict[str, object]:
        asset_url = expect_string(payload, "asset_url")
        filename = expect_string(payload, "suggested_filename")
        saved = checker.download_asset(url=asset_url, suggested_filename=filename)
        return {"saved_path": saved}

    return {
        "updates.check_latest": check_latest,
        "updates.open_release_page": open_release_page,
        "updates.download_asset": download_asset,
    }


def register(
    router: BridgeRouter,
    *,
    checker: UpdateChecker,
    current_version: str,
) -> None:
    for method, handler in _build_handlers(checker, current_version).items():
        router.register(method, handler)  # type: ignore[arg-type]


def platform_key() -> str:
    return _platform_key()


__all__ = [
    "NullUpdateChecker",
    "UpdateChecker",
    "platform_key",
    "register",
]
