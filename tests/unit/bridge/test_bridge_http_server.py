"""Subprocess integration test for the bridge HTTP server.

Spawns ``python -m transoria.bridge.http_server`` on a random port, walks the
``/api`` endpoints over HTTP using stdlib ``urllib``, and tears the process
down cleanly.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_ready(url: str, *, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_exc = exc
        time.sleep(0.1)
    raise AssertionError(f"HTTP server did not become ready in {timeout}s: {last_exc}")


def _post(url: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except urllib.error.HTTPError as exc:
        # The HTTP server returns BridgeError envelopes with HTTP 4xx/5xx.
        payload = json.loads(exc.read().decode("utf-8"))
        return exc.code, payload


@pytest.fixture(scope="module")
def http_server(tmp_path_factory: pytest.TempPathFactory):
    port = _free_port()
    cache_root = tmp_path_factory.mktemp("http-server-cache")
    static_root = tmp_path_factory.mktemp("http-static")
    (static_root / "assets").mkdir()
    (static_root / "index.html").write_text("<html>Transoria</html>", encoding="utf-8")
    (static_root / "assets" / "app.js").write_text("console.log('x')", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "transoria.bridge.http_server",
            "--port",
            str(port),
            "--cache-root",
            str(cache_root),
            "--static-root",
            str(static_root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_ready(f"http://127.0.0.1:{port}/api/health")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_health_returns_ok(http_server: str):
    with urllib.request.urlopen(f"{http_server}/api/health", timeout=5) as response:
        assert response.status == 200
        assert json.loads(response.read().decode("utf-8")) == {"ok": True}


def test_api_methods_lists_methods(http_server: str):
    with urllib.request.urlopen(f"{http_server}/api/_methods", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    methods = payload["methods"]
    assert len(methods) == 152
    for method in (
        "app.get_metadata",
        "translation.start_task",
        "glossary.read_artifacts",
        "glossary_review.read_report",
        "glossary_review.discover_inputs",
        "glossary_review.read_final",
        "glossary_review.delete_final_rows",
        "glossary_review.restore_deleted_report_row",
        "replacement.import_rules",
        "epub_compress.preview",
        "epub_compress.read_report",
        "epub_convert.preview",
        "epub_convert.read_report",
        "txt_to_epub.preview",
        "txt_to_epub.scan_toc",
        "txt_to_epub.list_styles",
        "settings.load_all",
        "model_profiles.list",
        "prompts.preview",
        "proofreading.resume_retranslate",
        "updates.check_latest",
    ):
        assert method in methods


def test_app_get_metadata(http_server: str):
    status, payload = _post(f"{http_server}/api/app.get_metadata", {})
    assert status == 200
    assert "app_version" in payload
    assert payload["platform"] in {"darwin", "win32", "linux"}


def test_unknown_method_returns_not_found(http_server: str):
    status, payload = _post(f"{http_server}/api/no.such.method", {})
    assert status == 400
    assert payload["code"] == "bridge.not_found"


def test_translation_start_validates_settings(http_server: str):
    status, payload = _post(
        f"{http_server}/api/translation.start_task", {"request_id": "rid"}
    )
    assert status == 400
    assert payload["code"] == "bridge.invalid_argument"
    assert payload["details"]["field"] == "input_folder"


def test_translation_pause_unknown_task_returns_not_found(http_server: str):
    """Pause is wired (D.1); unknown task ids surface ``bridge.not_found``."""

    status, payload = _post(
        f"{http_server}/api/translation.pause_task", {"task_id": "missing"}
    )
    assert status == 400
    assert payload["code"] == "bridge.not_found"


def test_invalid_json_body_returns_invalid_argument(http_server: str):
    request = urllib.request.Request(
        f"{http_server}/api/app.get_metadata",
        data=b"not-json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as _:
            assert False, "expected HTTPError"
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
    assert payload["code"] == "bridge.invalid_argument"


def test_static_root_serves_index(http_server: str):
    with urllib.request.urlopen(f"{http_server}/", timeout=5) as response:
        assert response.status == 200
        assert "Transoria" in response.read().decode("utf-8")
