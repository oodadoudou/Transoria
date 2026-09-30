from __future__ import annotations

import subprocess
import sys
import tomllib
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
        for name in ("_refresh_egg_info", "_write_distribution_artifacts", "_write_launch_bat"):
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
