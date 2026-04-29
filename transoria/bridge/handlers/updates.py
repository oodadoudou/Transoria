"""``updates.*`` bridge handlers.

Update v1 is conservative: check, compare, show, open, download. The
network calls live behind a :class:`UpdateChecker` Protocol so tests and
dev runs can inject a stub. The default checker hits GitHub; until the
release pipeline exists it returns "no updates" so the UI can render a
clean state.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

import httpx

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


@dataclass(frozen=True)
class GithubReleaseChecker:
    repository: str = "doudouda/Transoria"
    downloads_dir: Path | None = None
    client_factory: Callable[..., object] | None = None

    def check_latest(
        self, *, channel: str, current_version: str
    ) -> Mapping[str, object]:
        releases = self._get_json(f"{self._api_base()}/releases")
        if not isinstance(releases, list):
            raise BridgeError(
                "update.malformed_response",
                "GitHub releases response must be a list.",
                retryable=True,
            )
        release = _select_release(releases, channel=channel)
        if release is None:
            tag = self._latest_tag()
            if tag is None:
                raise BridgeError.not_found(
                    "GitHub repository has no releases or tags.",
                    details={"repository": self.repository},
                )
            latest_version = str(tag.get("name") or "")
            release_url = str(tag.get("zipball_url") or "")
            return {
                "current_version": current_version,
                "latest_version": latest_version,
                "is_newer_available": _is_newer(latest_version, current_version),
                "release_notes_markdown": "",
                "release_url": release_url,
                "published_at": "",
                "asset": None,
            }
        latest_version = str(release.get("tag_name") or release.get("name") or "")
        return {
            "current_version": current_version,
            "latest_version": latest_version,
            "is_newer_available": _is_newer(latest_version, current_version),
            "release_notes_markdown": str(release.get("body") or ""),
            "release_url": str(release.get("html_url") or ""),
            "published_at": str(release.get("published_at") or ""),
            "asset": _matching_asset(release),
        }

    def open_release_page(self, url: str) -> None:
        if not webbrowser.open(url):
            raise BridgeError(
                "bridge.io_error",
                "could not open release page.",
                retryable=False,
                details={"url": url},
            )

    def download_asset(
        self, *, url: str, suggested_filename: str
    ) -> str:
        filename = _safe_filename(suggested_filename)
        directory = self.downloads_dir or Path.home() / "Downloads"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        with self._client(timeout=60.0) as client:
            response = client.get(url)
        if response.status_code >= 400:
            raise BridgeError(
                "update.network_unavailable",
                f"asset download failed with HTTP {response.status_code}.",
                retryable=True,
                details={"url": url},
            )
        path.write_bytes(response.content)
        return str(path)

    def _latest_tag(self) -> Mapping[str, object] | None:
        tags = self._get_json(f"{self._api_base()}/tags")
        if isinstance(tags, list) and tags and isinstance(tags[0], Mapping):
            return tags[0]
        return None

    def _get_json(self, url: str) -> object:
        with self._client(timeout=15.0) as client:
            response = client.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Transoria UpdateChecker",
                },
            )
        if response.status_code == 404:
            raise BridgeError.not_found(
                "GitHub release endpoint was not found.",
                details={"repository": self.repository, "url": url},
            )
        if response.status_code >= 400:
            raise BridgeError(
                "update.network_unavailable",
                f"GitHub returned HTTP {response.status_code}.",
                retryable=True,
                details={"repository": self.repository},
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BridgeError(
                "update.malformed_response",
                f"GitHub returned invalid JSON: {exc}",
                retryable=True,
            ) from exc

    def _client(self, *, timeout: float):
        factory = self.client_factory or httpx.Client
        return factory(timeout=timeout)

    def _api_base(self) -> str:
        return f"https://api.github.com/repos/{self.repository.strip('/')}"


def _platform_key() -> str:
    raw = sys.platform
    return {"darwin": "darwin", "win32": "win32", "linux": "linux"}.get(raw, raw)


def _select_release(
    releases: list[object], *, channel: str
) -> Mapping[str, object] | None:
    for item in releases:
        if not isinstance(item, Mapping):
            continue
        is_prerelease = bool(item.get("prerelease"))
        is_draft = bool(item.get("draft"))
        if is_draft:
            continue
        if channel == "stable" and is_prerelease:
            continue
        return item
    return None


def _matching_asset(release: Mapping[str, object]) -> dict[str, object] | None:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    platform = _platform_key()
    fallback: Mapping[str, object] | None = None
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        name = str(asset.get("name") or "")
        lowered = name.lower()
        if platform in lowered or _platform_alias(platform) in lowered:
            return _asset_payload(asset, platform=platform)
        if fallback is None:
            fallback = asset
    if fallback is None:
        return None
    return _asset_payload(fallback, platform=platform)


def _asset_payload(asset: Mapping[str, object], *, platform: str) -> dict[str, object]:
    return {
        "name": str(asset.get("name") or ""),
        "download_url": str(asset.get("browser_download_url") or ""),
        "size_bytes": int(asset.get("size") or 0),
        "platform": platform,
    }


def _platform_alias(platform: str) -> str:
    return {"darwin": "mac", "win32": "windows", "linux": "linux"}.get(
        platform, platform
    )


def _is_newer(latest: str, current: str) -> bool:
    latest_parts = _version_parts(latest)
    current_parts = _version_parts(current)
    if latest_parts and current_parts:
        return latest_parts > current_parts
    return latest != current


def _version_parts(value: str) -> tuple[int, ...]:
    text = value.strip().lstrip("vV")
    parts: list[int] = []
    for raw in text.replace("-", ".").split("."):
        digits = ""
        for char in raw:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _safe_filename(value: str) -> str:
    name = Path(value).name
    if not name or name in {".", ".."}:
        raise BridgeError.invalid_argument(
            "suggested_filename must contain a filename.",
            field="suggested_filename",
        )
    return name


def _build_handlers(
    checker: UpdateChecker,
    current_version: str,
) -> dict[str, Callable[[Mapping[str, object]], dict[str, object]]]:
    def check_latest(payload: Mapping[str, object]) -> dict[str, object]:
        channel = os.environ.get("TRANSORIA_UPDATE_CHANNEL") or payload.get(
            "channel", "stable"
        )
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
    "GithubReleaseChecker",
    "NullUpdateChecker",
    "UpdateChecker",
    "platform_key",
    "register",
]
