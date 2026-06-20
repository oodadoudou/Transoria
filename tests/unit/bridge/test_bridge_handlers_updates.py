"""Tests for ``transoria.bridge.handlers.updates``."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.updates import (
    GithubReleaseChecker,
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

    def apply_update_windows(
        self,
        *,
        url: str,
        suggested_filename: str,
        target_version: str,
    ) -> Mapping[str, object]:
        return {
            "staging_root": f"/tmp/staging/{suggested_filename}",
            "install_root": "/tmp/install",
            "shutdown_in_seconds": 2,
            "echoed_version": target_version,
        }


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = str(payload)

    def json(self) -> object:
        return self._payload


class FakeHttpClient:
    def __init__(self, responses: dict[str, FakeResponse], *, timeout: float) -> None:
        self.responses = responses
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url: str, **_kwargs):
        return self.responses[url]


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


def test_github_checker_selects_stable_release_and_matching_platform(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("sys.platform", "darwin")
    responses = {
        "https://api.github.com/repos/owner/repo/releases": FakeResponse(
            200,
            [
                {
                    "tag_name": "v2.0.0-beta",
                    "prerelease": True,
                    "draft": False,
                },
                {
                    "tag_name": "v1.2.0",
                    "name": "v1.2.0",
                    "body": "notes",
                    "html_url": "https://github.com/owner/repo/releases/v1.2.0",
                    "published_at": "2026-04-28T00:00:00Z",
                    "prerelease": False,
                    "draft": False,
                    "assets": [
                        {
                            "name": "Transoria.dmg",
                            "browser_download_url": "https://example/darwin.dmg",
                            "size": 123,
                        }
                    ],
                },
            ],
        )
    }
    checker = GithubReleaseChecker(
        repository="owner/repo",
        downloads_dir=tmp_path,
        client_factory=lambda timeout: FakeHttpClient(responses, timeout=timeout),
    )

    payload = checker.check_latest(channel="stable", current_version="1.0.0")

    assert payload["latest_version"] == "v1.2.0"
    assert payload["is_newer_available"] is True
    assert payload["release_notes_markdown"] == "notes"
    assert payload["asset"]["download_url"] == "https://example/darwin.dmg"


def test_github_checker_windows_asset_must_be_zip(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    responses = {
        "https://api.github.com/repos/owner/repo/releases": FakeResponse(
            200,
            [
                {
                    "tag_name": "v1.2.0",
                    "name": "v1.2.0",
                    "body": "notes",
                    "html_url": "https://github.com/owner/repo/releases/v1.2.0",
                    "published_at": "2026-04-28T00:00:00Z",
                    "prerelease": False,
                    "draft": False,
                    "assets": [
                        {
                            "name": "Transoria.dmg",
                            "browser_download_url": "https://example/darwin.dmg",
                            "size": 123,
                        },
                        {
                            "name": "Transoria-windows.exe",
                            "browser_download_url": "https://example/windows.exe",
                            "size": 456,
                        },
                    ],
                },
            ],
        )
    }
    checker = GithubReleaseChecker(
        repository="owner/repo",
        downloads_dir=tmp_path,
        client_factory=lambda timeout: FakeHttpClient(responses, timeout=timeout),
    )

    payload = checker.check_latest(channel="stable", current_version="1.0.0")

    assert payload["asset"] is None


def test_github_checker_windows_selects_generic_zip(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    responses = {
        "https://api.github.com/repos/owner/repo/releases": FakeResponse(
            200,
            [
                {
                    "tag_name": "v1.2.0",
                    "name": "v1.2.0",
                    "body": "notes",
                    "html_url": "https://github.com/owner/repo/releases/v1.2.0",
                    "published_at": "2026-04-28T00:00:00Z",
                    "prerelease": False,
                    "draft": False,
                    "assets": [
                        {
                            "name": "Transoria.dmg",
                            "browser_download_url": "https://example/darwin.dmg",
                            "size": 123,
                        },
                        {
                            "name": "Transoria.zip",
                            "browser_download_url": "https://example/windows.zip",
                            "size": 456,
                        },
                    ],
                },
            ],
        )
    }
    checker = GithubReleaseChecker(
        repository="owner/repo",
        downloads_dir=tmp_path,
        client_factory=lambda timeout: FakeHttpClient(responses, timeout=timeout),
    )

    payload = checker.check_latest(channel="stable", current_version="1.0.0")

    assert payload["asset"]["download_url"] == "https://example/windows.zip"


def test_github_checker_downloads_asset(tmp_path):
    responses = {
        "https://example/file.zip": FakeResponse(
            200,
            {},
            content=b"zip-bytes",
        )
    }
    checker = GithubReleaseChecker(
        repository="owner/repo",
        downloads_dir=tmp_path,
        client_factory=lambda timeout: FakeHttpClient(responses, timeout=timeout),
    )

    saved = checker.download_asset(
        url="https://example/file.zip",
        suggested_filename="../file.zip",
    )

    assert Path(saved).read_bytes() == b"zip-bytes"
    assert Path(saved).parent == tmp_path
# Windows auto-update


class FakeStreamResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self, chunk_size: int = 65536):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset : offset + chunk_size]


class FakeStreamingHttpClient(FakeHttpClient):
    """Minimal fake that supports both ``get`` (one-shot) and the
    ``stream`` context manager used by the apply path."""

    def stream(self, _method: str, url: str):
        response = self.responses[url]
        return FakeStreamResponse(response.status_code, response.content)


def test_apply_update_rejects_non_windows_platform(monkeypatch):
    """On macOS/linux the handler must refuse before touching disk or
    spawning anything — no staging dir, no subprocess."""
    monkeypatch.setattr("sys.platform", "darwin")
    checker = GithubReleaseChecker(repository="owner/repo")

    with pytest.raises(BridgeError) as caught:
        checker.apply_update_windows(
            url="https://example/x.zip",
            suggested_filename="x.zip",
            target_version="1.0.1",
        )

    assert caught.value.code == "update.unsupported_platform"


def test_apply_update_rejects_when_not_packaged(monkeypatch):
    """Source-mode (no ``sys.frozen``) must refuse so a dev shell can't
    accidentally trash its working tree by downloading a release zip."""
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delattr("sys.frozen", raising=False)
    checker = GithubReleaseChecker(repository="owner/repo")

    with pytest.raises(BridgeError) as caught:
        checker.apply_update_windows(
            url="https://example/x.zip",
            suggested_filename="x.zip",
            target_version="1.0.1",
        )

    assert caught.value.code == "update.not_packaged"


def test_apply_update_rejects_unexpected_install_layout(monkeypatch, tmp_path):
    """If Transoria.exe isn't next to ``sys.executable``, refuse —
    something between PyInstaller and us moved the binary, and we
    don't want to splat files in the wrong place."""
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    fake_exe = tmp_path / "OtherName.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr("sys.executable", str(fake_exe))
    checker = GithubReleaseChecker(repository="owner/repo")

    with pytest.raises(BridgeError) as caught:
        checker.apply_update_windows(
            url="https://example/x.zip",
            suggested_filename="x.zip",
            target_version="1.0.1",
        )

    assert caught.value.code == "update.unexpected_install_layout"


def test_apply_update_rejects_malformed_archive(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "Transoria.exe").write_bytes(b"")
    monkeypatch.setattr("sys.executable", str(install_root / "Transoria.exe"))
    # Stream a non-zip body so zipfile.BadZipFile fires on validate.
    responses = {
        "https://example/bad.zip": FakeResponse(
            200, {}, content=b"not actually a zip"
        )
    }
    # No subprocess / shutdown should be reached — but mock anyway so a
    # bug that gets past the validate guard doesn't reboot the test runner.
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *args, **kwargs: spawned.append(list(args[0]) if args else []),
    )
    monkeypatch.setattr(
        "transoria.bridge.handlers.updates._schedule_app_shutdown_for_update",
        lambda: None,
    )
    checker = GithubReleaseChecker(
        repository="owner/repo",
        client_factory=lambda timeout: FakeStreamingHttpClient(
            responses, timeout=timeout
        ),
    )

    with pytest.raises(BridgeError) as caught:
        checker.apply_update_windows(
            url="https://example/bad.zip",
            suggested_filename="bad.zip",
            target_version="1.0.1",
        )

    assert caught.value.code == "update.malformed_archive"
    assert spawned == []


def test_apply_update_happy_path_writes_bat_and_spawns(monkeypatch, tmp_path):
    """End-to-end (with mocks at the OS boundary): download a real zip
    that has the expected ``Transoria/Transoria.exe`` payload, verify
    the helper bat is materialized with the correct PID + paths, and
    that subprocess.Popen is invoked with the bat path. The shutdown
    daemon is mocked so the test doesn't actually exit the runner."""
    import io
    import zipfile as _zipfile

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "Transoria.exe").write_bytes(b"current-exe")
    monkeypatch.setattr("sys.executable", str(install_root / "Transoria.exe"))

    # Build a real zip in memory with the expected layout.
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Transoria/Transoria.exe", b"new-exe")
        zf.writestr("Transoria/_internal/runtime.dll", b"new-dll")
        zf.writestr("Transoria/Launch_Transoria.bat", b"@echo off\n")
    zip_bytes = buf.getvalue()

    responses = {
        "https://example/Transoria-windows.zip": FakeResponse(
            200, {}, content=zip_bytes
        )
    }

    spawned: list[dict[str, object]] = []

    def fake_popen(args, **kwargs):
        spawned.append({"args": list(args), "kwargs": dict(kwargs)})
        return object()

    shutdown_calls: list[bool] = []
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "transoria.bridge.handlers.updates._schedule_app_shutdown_for_update",
        lambda: shutdown_calls.append(True),
    )
    # Per-PID staging dir uses ``tempfile.gettempdir()``; redirect to
    # tmp_path so the test cleans itself up.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    checker = GithubReleaseChecker(
        repository="owner/repo",
        client_factory=lambda timeout: FakeStreamingHttpClient(
            responses, timeout=timeout
        ),
    )

    response = checker.apply_update_windows(
        url="https://example/Transoria-windows.zip",
        suggested_filename="Transoria-windows.zip",
        target_version="1.0.1",
    )

    assert response["install_root"] == str(install_root)
    assert response["shutdown_in_seconds"] == 2
    staging = Path(response["staging_root"])
    assert staging.exists()
    apply_bat = staging / "apply.bat"
    assert apply_bat.exists()
    bat_text = apply_bat.read_text(encoding="utf-8")
    install_root_win = str(install_root).replace("/", "\\")
    staging_win = str(staging).replace("/", "\\")
    assert install_root_win in bat_text
    assert staging_win in bat_text
    assert "1.0.1" in bat_text
    # Excluded dirs appear in the robocopy line so user files survive.
    # ``User Data`` is the portable-Windows cache root (settings,
    # model_profiles, tasks/...) — the central cache lives under it,
    # so this exclusion is what keeps task progress / proofreading
    # data alive across an in-place auto-update.
    import re

    assert re.search(r'/XD\s+"User Data"\s+Input\s+Output', bat_text), (
        "Apply.bat must exclude the User Data / Input / Output dirs "
        "from robocopy so settings + task cache survive auto-update."
    )
    assert "User Data" in bat_text
    assert "Input" in bat_text
    assert "Output" in bat_text
    # Bat must not contain forward-slash paths (cmd.exe / robocopy
    # don't reliably handle them in quoted strings).
    assert "/extracted/" not in bat_text
    # Spawn was issued exactly once with cmd.exe and the bat path.
    assert len(spawned) == 1
    spawn = spawned[0]
    assert spawn["args"][0] == "cmd.exe"
    assert spawn["args"][-1] == str(apply_bat)
    assert shutdown_calls == [True]


def test_apply_update_accepts_direct_payload_root(monkeypatch, tmp_path):
    import io
    import zipfile as _zipfile

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "Transoria.exe").write_bytes(b"current-exe")
    monkeypatch.setattr("sys.executable", str(install_root / "Transoria.exe"))

    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Transoria.exe", b"new-exe")
        zf.writestr("_internal/runtime.dll", b"new-dll")
    responses = {
        "https://example/Transoria.zip": FakeResponse(
            200, {}, content=buf.getvalue()
        )
    }
    spawned: list[dict[str, object]] = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda args, **kwargs: spawned.append({"args": list(args), "kwargs": kwargs}),
    )
    monkeypatch.setattr(
        "transoria.bridge.handlers.updates._schedule_app_shutdown_for_update",
        lambda: None,
    )
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    checker = GithubReleaseChecker(
        repository="owner/repo",
        client_factory=lambda timeout: FakeStreamingHttpClient(
            responses, timeout=timeout
        ),
    )

    response = checker.apply_update_windows(
        url="https://example/Transoria.zip",
        suggested_filename="Transoria.zip",
        target_version="1.0.1",
    )

    bat_text = (Path(response["staging_root"]) / "apply.bat").read_text(
        encoding="utf-8"
    )
    assert "\\extracted" in bat_text
    assert "\\extracted\\Transoria" not in bat_text
    assert len(spawned) == 1
