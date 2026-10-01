from __future__ import annotations

import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement

import build_macos
import build_windows


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_MODULES = {
    "lxml": "lxml",
    "html5lib": "html5lib",
    "httpx": "httpx",
    "json-repair": "json_repair",
    "openpyxl": "openpyxl",
    "pillow": "PIL",
    "regex": "regex",
    "tinycss2": "tinycss2",
    "cssselect2": "cssselect2",
    "fonttools": "fontTools.subset",
    "spylls": "spylls.hunspell",
    "chardet": "chardet",
}


def test_requirements_uses_project_extras_without_duplicate_versions():
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    assert [line for line in lines if line and not line.startswith("#")] == [
        "-e .[gui,dev,build]"
    ]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    requirements = {Requirement(value).name: Requirement(value) for value in project["dependencies"]}
    assert requirements.keys() == RUNTIME_MODULES.keys()
    assert "woff" in requirements["fonttools"].extras
    assert {"gui", "dev", "build"} <= project["optional-dependencies"].keys()


@pytest.mark.parametrize("builder", [build_macos, build_windows])
def test_build_preflight_covers_runtime_and_font_codecs(builder):
    assert set(RUNTIME_MODULES.values()) | {
        "fontTools.ttLib.woff2", "brotli", "zopfli.zlib", "webview",
    } <= set(builder.REQUIRED_RUNTIME_IMPORTS)


@pytest.mark.parametrize("existing_node_modules", [False, True])
def test_windows_build_installs_locked_frontend_deps(
    existing_node_modules, monkeypatch, tmp_path
):
    if existing_node_modules:
        # Simulate an old install that predates the EPUB editor dependencies.
        (tmp_path / "node_modules").mkdir()
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build_windows, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(build_windows, "_npm", lambda: "npm.cmd")
    monkeypatch.setenv("NODE_ENV", "production")
    commands = []
    monkeypatch.setattr(
        build_windows, "_run",
        lambda command, **kwargs: commands.append((command, kwargs["cwd"])),
    )

    build_windows._ensure_frontend_deps()

    assert commands == [(["npm.cmd", "ci", "--include=dev"], tmp_path)]


@pytest.mark.parametrize("builder", [build_macos, build_windows])
def test_build_reports_all_missing_editor_dependencies(builder, monkeypatch):
    missing = {"cssselect2", "fontTools.subset", "spylls.hunspell", "zopfli.zlib"}
    checked = []

    def probe(command, **kwargs):
        assert command[:2] == [sys.executable, "-c"]
        module = command[2].removeprefix("import ")
        checked.append(module)
        return subprocess.CompletedProcess(command, int(module in missing))

    monkeypatch.setattr(builder.subprocess, "run", probe)
    with pytest.raises(SystemExit) as error:
        builder._require_runtime_imports()
    assert checked == list(builder.REQUIRED_RUNTIME_IMPORTS)
    assert all(module in str(error.value) for module in missing)
    assert 'python -m pip install -e ".[gui,build]"' in str(error.value)


@pytest.mark.parametrize("builder", [build_macos, build_windows])
def test_build_command_collects_editor_and_platform_modules(builder, monkeypatch, tmp_path):
    commands = []
    for name in ("DIST_DIR", "WORK_DIR", "SPEC_DIR"):
        monkeypatch.setattr(builder, name, tmp_path / name)
    monkeypatch.setattr(builder, "ICON_PATH", tmp_path / "missing-icon")
    for name in (
        "_require_frontend_dist", "_require_pyinstaller", "_require_runtime_imports",
        "_note_unbundled_local_state", "_verify_spec_excludes_local_state",
    ):
        monkeypatch.setattr(builder, name, lambda: None)
    monkeypatch.setattr(builder, "_run", lambda command, **kwargs: commands.append(command))
    flags = ["build", "--skip-frontend", "--no-smoke-test"]
    if builder is build_macos:
        flags += ["--allow-non-macos", "--skip-dmg"]
        backend = {"webview.platforms.cocoa"}
    else:
        flags += ["--allow-non-windows", "--no-webview2-bootstrapper"]
        backend = {"webview.platforms.winforms", "webview.platforms.edgechromium"}
        for name in (
            "_refresh_egg_info", "_write_distribution_artifacts", "_write_launch_bat",
            "_create_release_zip",
        ):
            monkeypatch.setattr(builder, name, lambda: None)
    monkeypatch.setattr(sys, "argv", flags)
    builder.main()
    assert len(commands) == 1
    command = commands[0]
    collected = {command[index + 1] for index, value in enumerate(command) if value == "--collect-submodules"}
    hidden = {command[index + 1] for index, value in enumerate(command) if value == "--hidden-import"}
    assert {"html5lib", "tinycss2", "cssselect2", "fontTools", "spylls"} <= collected
    assert {"brotli", "zopfli.zlib"} | backend <= hidden
    assert command[:3] == [sys.executable, "-m", "PyInstaller"]
    assert command[-1] == str(builder.ROOT / "app.py")


def test_windows_zip_replaces_previous_release_with_current_build(monkeypatch, tmp_path):
    dist = tmp_path / "pyinstaller" / "windows"
    app = dist / "Transoria"
    app.mkdir(parents=True)
    (app / "Transoria.exe").write_bytes(b"new executable")
    (app / "_internal").mkdir()
    (app / "_internal" / "resource.txt").write_text("new resource")
    (app / "Launch_Transoria.bat").write_text("launcher")
    target = tmp_path / "Transoria.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("obsolete.txt", "old release")
    monkeypatch.setattr(build_windows, "DIST_DIR", dist)
    monkeypatch.setattr(build_windows, "APP_DIR", app)
    monkeypatch.setattr(build_windows, "ZIP_PATH", target)

    assert build_windows._create_release_zip() == target

    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert "obsolete.txt" not in archive.namelist()
        assert archive.read("Transoria/Transoria.exe") == b"new executable"
        assert archive.read("Transoria/_internal/resource.txt") == b"new resource"
        assert archive.read("Transoria/Launch_Transoria.bat") == b"launcher"
    assert not list(tmp_path.glob(".transoria-zip-*"))


def test_windows_zip_failure_preserves_previous_release(monkeypatch, tmp_path):
    app = tmp_path / "Transoria"
    app.mkdir()
    target = tmp_path / "Transoria.zip"
    target.write_bytes(b"previous release")
    monkeypatch.setattr(build_windows, "APP_DIR", app)
    monkeypatch.setattr(build_windows, "ZIP_PATH", target)

    def fail_archive(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(build_windows.shutil, "make_archive", fail_archive)
    with pytest.raises(OSError, match="disk full"):
        build_windows._create_release_zip()
    assert target.read_bytes() == b"previous release"
    assert not list(tmp_path.glob(".transoria-zip-*"))


@pytest.mark.parametrize("smoke_fails", [False, True])
def test_windows_build_zips_automatically_only_after_success(
    smoke_fails, monkeypatch, tmp_path
):
    events = []
    for name in ("DIST_DIR", "WORK_DIR", "SPEC_DIR"):
        monkeypatch.setattr(build_windows, name, tmp_path / name)
    monkeypatch.setattr(build_windows, "ICON_PATH", tmp_path / "missing-icon")
    for name in (
        "_require_frontend_dist", "_require_pyinstaller", "_require_runtime_imports",
        "_refresh_egg_info", "_note_unbundled_local_state",
        "_verify_spec_excludes_local_state", "_write_distribution_artifacts",
        "_write_launch_bat",
    ):
        monkeypatch.setattr(build_windows, name, lambda: None)
    monkeypatch.setattr(build_windows, "_run", lambda *args, **kwargs: events.append("package"))

    def smoke():
        events.append("smoke")
        if smoke_fails:
            raise SystemExit("smoke failed")

    def archive():
        events.append("zip")
        return tmp_path / "Transoria.zip"

    monkeypatch.setattr(build_windows, "_smoke_test_built_exe", smoke)
    monkeypatch.setattr(build_windows, "_create_release_zip", archive)
    monkeypatch.setattr(
        sys, "argv",
        ["build", "--skip-frontend", "--allow-non-windows", "--no-webview2-bootstrapper"],
    )
    if smoke_fails:
        with pytest.raises(SystemExit, match="smoke failed"):
            build_windows.main()
        assert events == ["package", "smoke"]
    else:
        build_windows.main()
        assert events == ["package", "smoke", "zip"]
