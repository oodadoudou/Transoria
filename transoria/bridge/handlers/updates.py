"""``updates.*`` bridge handlers.

Update v1 is conservative: check, compare, show, open, download. The
network calls live behind a :class:`UpdateChecker` Protocol so tests and
dev runs can inject a stub. The default checker hits GitHub; until the
release pipeline exists it returns "no updates" so the UI can render a
clean state.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile
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

    def apply_update_windows(
        self,
        *,
        url: str,
        suggested_filename: str,
        target_version: str,
    ) -> Mapping[str, object]: ...


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

    def apply_update_windows(
        self,
        *,
        url: str,
        suggested_filename: str,
        target_version: str,
    ) -> Mapping[str, object]:
        raise BridgeError(
            "bridge.io_error",
            "Windows auto-update is unavailable in this build.",
            retryable=False,
            details={"url": url, "filename": suggested_filename},
        )


@dataclass(frozen=True)
class GithubReleaseChecker:
    repository: str = "doudouda/Transoria"
    downloads_dir: Path | None = None
    client_factory: Callable[..., object] | None = None
    # Read on every HTTP call so the user can change ``app.proxy_url``
    # in App Settings and the next "Check for updates" / "Download asset"
    # click picks it up without restarting. Returning ``""`` / ``None``
    # falls back to httpx defaults (which honor ``HTTPS_PROXY`` env var).
    proxy_provider: Callable[[], str | None] | None = None

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

    def apply_update_windows(
        self,
        *,
        url: str,
        suggested_filename: str,
        target_version: str,
    ) -> Mapping[str, object]:
        """Stage a Windows update and spawn a helper batch script.

        Steps:
        1. Refuse if not on Windows or not packaged (PyInstaller frozen).
        2. Locate install root from ``sys.executable``; sanity-check that
           ``Transoria.exe`` lives there.
        3. Create a per-PID staging dir under the OS temp area so cleanup
           never touches the install tree even if it crashes.
        4. Stream-download the zip (long timeout for slow networks).
        5. Validate (zipfile open) and extract to staging.
        6. Locate the inner ``Transoria/`` payload.
        7. Materialize ``apply.bat`` with PID + paths interpolated.
        8. Spawn it detached in a fresh console so the user sees progress.
        9. Schedule a graceful App shutdown ~2 s later so the bridge HTTP
           response can flush back to the renderer first.

        The bat preserves user-owned dirs (``User Data``, ``Input``,
        ``Output``) via robocopy ``/XD``. The user must relaunch via
        ``Launch_Transoria.bat`` after the helper finishes.
        """

        if sys.platform != "win32":
            raise BridgeError(
                "update.unsupported_platform",
                "Windows auto-update is only available on Windows.",
                retryable=False,
                details={"platform": sys.platform},
            )
        if not getattr(sys, "frozen", False):
            raise BridgeError(
                "update.not_packaged",
                "Auto-update requires the packaged build; running from source must update via git.",
                retryable=False,
            )

        install_root = Path(sys.executable).resolve().parent
        if not (install_root / "Transoria.exe").exists():
            raise BridgeError(
                "update.unexpected_install_layout",
                "Could not locate Transoria.exe next to the running executable.",
                retryable=False,
                details={"install_root": str(install_root)},
            )

        staging = Path(tempfile.gettempdir()) / f"transoria-updater-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        zip_path = staging / _safe_filename(suggested_filename)
        self._stream_download(url, zip_path)

        try:
            with zipfile.ZipFile(zip_path) as archive:
                members = archive.namelist()
            if not members:
                raise BridgeError(
                    "update.malformed_archive",
                    "Downloaded zip is empty.",
                    retryable=True,
                )
        except zipfile.BadZipFile as exc:
            raise BridgeError(
                "update.malformed_archive",
                "Downloaded file is not a valid zip archive.",
                retryable=True,
            ) from exc

        extracted = staging / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extracted)

        payload_root: Path | None = None
        for child in extracted.iterdir():
            if child.is_dir() and (child / "Transoria.exe").exists():
                payload_root = child
                break
        if payload_root is None:
            raise BridgeError(
                "update.malformed_archive",
                "Downloaded archive does not contain a Transoria/ payload with Transoria.exe.",
                retryable=False,
                details={"members_sampled": members[:5]},
            )

        apply_bat = staging / "apply.bat"
        # Force backslashes so the bat text is valid Windows syntax
        # regardless of which OS the substitution runs on. In production
        # (sys.platform=="win32") this is already the form Path emits;
        # in tests that monkeypatch sys.platform on a POSIX host it
        # ensures the rendered bat is what Windows would actually run.
        apply_bat.write_text(
            _APPLY_BAT_TEMPLATE.format(
                target_pid=os.getpid(),
                payload_root=_as_windows_path(payload_root),
                install_root=_as_windows_path(install_root),
                staging_root=_as_windows_path(staging),
                target_version=target_version or "(unknown)",
            ),
            encoding="utf-8",
        )

        # CREATE_NEW_CONSOLE makes the helper's progress visible to the
        # user; CREATE_NEW_PROCESS_GROUP detaches it from the parent so
        # the App can exit independently. close_fds + DEVNULL pipes
        # ensure no inherited handles keep file locks alive.
        CREATE_NEW_CONSOLE = 0x00000010
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            ["cmd.exe", "/c", str(apply_bat)],
            cwd=str(staging),
            creationflags=CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _schedule_app_shutdown_for_update()

        return {
            "staging_root": str(staging),
            "install_root": str(install_root),
            "shutdown_in_seconds": _SHUTDOWN_DELAY_SECONDS,
        }

    def _stream_download(self, url: str, dest: Path) -> None:
        timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        with self._client(timeout=timeout) as client:
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise BridgeError(
                        "update.network_unavailable",
                        f"download failed with HTTP {response.status_code}.",
                        retryable=True,
                        details={"url": url},
                    )
                with dest.open("wb") as fh:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        fh.write(chunk)
        # Defense-in-depth: a 0-byte file means the response body was
        # empty even though the status said success. Most likely an
        # unfollowed redirect (see ``_client`` follow_redirects note),
        # but could also be a misconfigured upstream. Fail loudly here
        # instead of letting ``zipfile.ZipFile`` produce the cryptic
        # ``BadZipFile`` error.
        if dest.stat().st_size == 0:
            raise BridgeError(
                "update.network_unavailable",
                "download produced a 0-byte file (likely an unfollowed redirect).",
                retryable=True,
                details={"url": url},
            )

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

    def _client(self, *, timeout: "float | httpx.Timeout"):
        if self.client_factory is not None:
            return self.client_factory(timeout=timeout)
        # ``follow_redirects=True`` is mandatory: GitHub release-asset
        # URLs (``releases/download/…``) are 302 redirects to the actual
        # CDN host. Without redirect-following httpx happily returns the
        # 302 with an empty body, which then writes a 0-byte file that
        # blows up downstream as ``BadZipFile: Downloaded file is not a
        # valid zip archive``. The same applies to ``/api/releases``
        # responses on rare occasions when GitHub redirects them.
        kwargs: dict[str, object] = {
            "timeout": timeout,
            "follow_redirects": True,
        }
        if self.proxy_provider is not None:
            try:
                proxy = (self.proxy_provider() or "").strip()
            except Exception:  # noqa: BLE001
                proxy = ""
            if proxy:
                kwargs["proxy"] = proxy
        return httpx.Client(**kwargs)

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
    """Pick the release asset that fits the running host.

    Priority:
    1. Name explicitly contains the platform marker (``windows`` / ``win32``
       / ``mac`` / ``darwin`` / ``linux``).
    2. File extension is the platform's conventional package format
       (``.zip``/``.exe`` for Windows, ``.dmg`` for macOS, ``.tar.gz`` /
       ``.AppImage`` for Linux). This catches the common case where a
       release uploads only one asset per platform with a generic name
       (e.g. ``Transoria.zip`` for Windows).
    3. First asset of any shape (last-resort fallback).
    """

    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    platform = _platform_key()
    alias = _platform_alias(platform)
    explicit: Mapping[str, object] | None = None
    by_extension: Mapping[str, object] | None = None
    fallback: Mapping[str, object] | None = None
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        name = str(asset.get("name") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if platform in lowered or alias in lowered:
            explicit = asset
            break
        if by_extension is None and _extension_matches_platform(
            lowered, platform
        ):
            by_extension = asset
        if fallback is None:
            fallback = asset
    chosen = explicit or by_extension or fallback
    if chosen is None:
        return None
    return _asset_payload(chosen, platform=platform)


def _extension_matches_platform(name_lower: str, platform: str) -> bool:
    if platform == "win32":
        return name_lower.endswith(".zip") or name_lower.endswith(".exe")
    if platform == "darwin":
        return name_lower.endswith(".dmg") or name_lower.endswith(".app.zip")
    if platform == "linux":
        return (
            name_lower.endswith(".tar.gz")
            or name_lower.endswith(".tgz")
            or name_lower.endswith(".appimage")
        )
    return False


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


# Curly-brace placeholders are str.format()-substituted at write time.
# Batch syntax has no literal `{` / `}`, so no escaping is needed.
# Excluded dirs (``User Data``, ``Input``, ``Output``) are matched by
# name against both the source and destination trees by robocopy.
_APPLY_BAT_TEMPLATE = """@echo off
setlocal EnableDelayedExpansion
title Transoria Updater

set "TARGET_PID={target_pid}"
set "PAYLOAD_ROOT={payload_root}"
set "INSTALL_ROOT={install_root}"
set "STAGING_ROOT={staging_root}"
set /a "WAIT_COUNT=0"

echo.
echo Transoria updater - applying {target_version}
echo Waiting for the running app to close...
:wait
tasklist /NH /FI "PID eq %TARGET_PID%" 2>nul | find /I "exe" >nul
if %errorlevel% equ 0 (
  set /a "WAIT_COUNT+=1"
  if !WAIT_COUNT! gtr 60 (
    echo.
    echo ERROR: Transoria did not close within 60 seconds.
    echo The downloaded update is staged at:
    echo   %STAGING_ROOT%
    echo You can apply it manually by copying the extracted Transoria folder
    echo over your install while Transoria is closed.
    pause
    exit /b 1
  )
  timeout /t 1 /nobreak >nul
  goto wait
)

echo App closed. Releasing file handles...
timeout /t 2 /nobreak >nul

echo Replacing files (preserving User Data, Input, Output)...
robocopy "%PAYLOAD_ROOT%" "%INSTALL_ROOT%" /E /XD "User Data" Input Output /R:5 /W:2
set "RC=%errorlevel%"

REM robocopy: 0 = no copy needed, 1-7 = success-with-info, 8+ = error
if %RC% gtr 7 (
  echo.
  echo ERROR: file replacement failed (robocopy code %RC%).
  echo The downloaded update is staged at:
  echo   %STAGING_ROOT%
  pause
  exit /b %RC%
)

echo Cleaning up staging files...
rmdir /S /Q "%STAGING_ROOT%" 2>nul

echo.
echo Update applied successfully!
echo Please double-click Launch_Transoria.bat to start the new version.
echo This window will close in 5 seconds.
timeout /t 5 /nobreak >nul
exit /b 0
"""


_SHUTDOWN_DELAY_SECONDS = 2


def _schedule_app_shutdown_for_update() -> None:
    """Trigger a graceful App shutdown a couple of seconds after we
    return, so the bridge HTTP response has time to flush back to the
    renderer (which then shows the "App is closing" countdown).

    Tries a clean ``webview.windows[*].destroy()`` first; falls back
    to ``os._exit`` so the helper bat is never left waiting on a
    hung process.
    """

    def _do_shutdown() -> None:
        time.sleep(_SHUTDOWN_DELAY_SECONDS)
        try:
            import webview  # noqa: PLC0415
        except ImportError:
            os._exit(0)
            return
        try:
            for window in list(getattr(webview, "windows", []) or []):
                try:
                    window.destroy()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            # Belt-and-suspenders: the helper bat polls for our PID, so
            # the process MUST exit. ``destroy()`` returns control to
            # ``webview.start`` which then unwinds — but if pywebview
            # gets stuck, hard-exit anyway.
            time.sleep(1.0)
            os._exit(0)

    threading.Thread(target=_do_shutdown, daemon=True).start()


def _as_windows_path(path: Path) -> str:
    return str(path).replace("/", "\\")


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

    def apply_update_windows(payload: Mapping[str, object]) -> dict[str, object]:
        asset_url = expect_string(payload, "asset_url")
        filename = expect_string(payload, "suggested_filename")
        target_version = str(payload.get("target_version") or "")
        return dict(
            checker.apply_update_windows(
                url=asset_url,
                suggested_filename=filename,
                target_version=target_version,
            )
        )

    return {
        "updates.check_latest": check_latest,
        "updates.open_release_page": open_release_page,
        "updates.download_asset": download_asset,
        "updates.apply_update_windows": apply_update_windows,
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
