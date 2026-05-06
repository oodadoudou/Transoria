#!/usr/bin/env python3
"""Build the macOS Transoria app with PyInstaller."""

from __future__ import annotations

import argparse
import socket
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
DIST_DIR = ROOT / "dist" / "pyinstaller" / "macos"
WORK_DIR = ROOT / "build" / "pyinstaller" / "macos"
SPEC_DIR = ROOT / "build" / "pyinstaller" / "specs"
DMG_STAGING_DIR = WORK_DIR / "dmg-root"
ICON_PATH = ROOT / "assets" / "icon.icns"

EXCLUDED_MODULES = (
    # macOS uses pywebview's Cocoa backend. Other backends pull Qt,
    # GTK, Windows .NET, or Android stacks when they exist in the
    # build environment.
    "webview.platforms.android",
    "webview.platforms.cef",
    "webview.platforms.edgechromium",
    "webview.platforms.gtk",
    "webview.platforms.mshtml",
    "webview.platforms.qt",
    "webview.platforms.win32",
    "webview.platforms.winforms",
    # Optional packages commonly present in Anaconda environments. The
    # app does not use them, but PyInstaller hooks can discover and
    # bundle them through optional integrations.
    "IPython",
    "PyQt5",
    "PySide2",
    "PySide6",
    "_pytest",
    "ipywidgets",
    "jupyter_client",
    "jupyter_core",
    "matplotlib",
    "notebook",
    "numpy",
    "numpydoc",
    "pandas",
    "pytest",
    "scipy",
    "sphinx",
)

REQUIRED_RUNTIME_IMPORTS = (
    "json_repair",
    "chardet",
    "lxml",
    "openpyxl",
    "PIL",
    "webview",
)

SMOKE_TEST_TIMEOUT_SECONDS = 8


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Transoria.app for macOS.")
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Reuse existing frontend/dist instead of running npm run build.",
    )
    parser.add_argument(
        "--allow-non-macos",
        action="store_true",
        help="Allow command construction on non-macOS hosts for CI validation.",
    )
    parser.add_argument(
        "--skip-dmg",
        action="store_true",
        help="Build only Transoria.app and skip DMG creation.",
    )
    parser.add_argument(
        "--no-smoke-test",
        action="store_true",
        help="Skip the post-build bridge startup smoke test.",
    )
    args = parser.parse_args()

    if sys.platform != "darwin" and not args.allow_non_macos:
        raise SystemExit("macOS builds must run on macOS.")

    if not args.skip_frontend:
        _ensure_frontend_deps()
        _run([_npm(), "run", "build"], cwd=FRONTEND_DIR)
    _require_frontend_dist()
    _require_pyinstaller()
    _require_runtime_imports()
    _note_unbundled_local_state()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "Transoria",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--add-data",
        f"{FRONTEND_DIST}:frontend/dist",
        "--add-data",
        f"{ROOT / 'pyproject.toml'}:.",
        "--collect-data",
        "webview",
        "--collect-data",
        "openpyxl",
        "--hidden-import",
        "webview.platforms.cocoa",
        # Lazy submodules invisible to the static analyzer — without
        # collect-submodules the bundled app boots and dies the first
        # time it touches one of these packages.
        "--collect-submodules",
        "json_repair",
        "--collect-submodules",
        "chardet",
        "--collect-submodules",
        "lxml",
    ]
    for module in EXCLUDED_MODULES:
        cmd.extend(["--exclude-module", module])
    if ICON_PATH.is_file():
        cmd.extend(["--icon", str(ICON_PATH)])
        print(f"[build] using icon: {ICON_PATH}")
    else:
        print(
            f"[build] no icon at {ICON_PATH} — using PyInstaller default. "
            "Run scripts/make_app_icons.py to generate one."
        )
    cmd.append(str(ROOT / "app.py"))
    _run(cmd, cwd=ROOT)
    _verify_spec_excludes_local_state()
    app_path = DIST_DIR / "Transoria.app"
    print(f"[build] macOS app: {app_path}")
    if not args.no_smoke_test:
        _smoke_test_built_app(app_path)
    if not args.skip_dmg:
        dmg_path = _create_dmg(app_path)
        print(f"[build] macOS dmg: {dmg_path}")


def _npm() -> str:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm not found. Install Node.js on the build machine.")
    return npm


def _ensure_frontend_deps() -> None:
    if (FRONTEND_DIR / "node_modules").is_dir():
        return
    print("[build] frontend/node_modules missing — running npm install")
    _run([_npm(), "install"], cwd=FRONTEND_DIR)


def _require_frontend_dist() -> None:
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit("frontend/dist is missing. Run without --skip-frontend.")


def _require_pyinstaller() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise SystemExit(
            "PyInstaller not found. Run `python -m pip install -e \".[gui,build]\"` "
            "on the build machine."
        )


def _require_runtime_imports() -> None:
    missing: list[str] = []
    for module in REQUIRED_RUNTIME_IMPORTS:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            missing.append(module)
    if missing:
        raise SystemExit(
            "missing runtime dependencies: "
            + ", ".join(missing)
            + ".\nRun `python -m pip install -e \".[gui,build]\"` on the "
            "build machine before retrying."
        )


def _note_unbundled_local_state() -> None:
    for path in (
        ROOT / ".env",
        ROOT / ".transoria-cache",
        ROOT / "model_profile_keys.json",
    ):
        if path.exists():
            print(f"[build] not bundled: {path}")


def _verify_spec_excludes_local_state() -> None:
    spec = SPEC_DIR / "Transoria.spec"
    if not spec.exists():
        return
    raw = spec.read_text(encoding="utf-8", errors="ignore")
    forbidden = (".transoria-cache", "model_profile_keys", ".env")
    leaked = [item for item in forbidden if item in raw]
    if leaked:
        raise SystemExit(f"PyInstaller spec contains local state: {leaked}")


def _create_dmg(app_path: Path) -> Path:
    if shutil.which("hdiutil") is None:
        raise SystemExit("hdiutil not found. DMG creation must run on macOS.")
    if not app_path.is_dir():
        raise SystemExit(f"app bundle not found: {app_path}")
    if DMG_STAGING_DIR.exists():
        shutil.rmtree(DMG_STAGING_DIR)
    DMG_STAGING_DIR.mkdir(parents=True)
    shutil.copytree(app_path, DMG_STAGING_DIR / "Transoria.app", symlinks=True)
    applications_link = DMG_STAGING_DIR / "Applications"
    if not applications_link.exists():
        applications_link.symlink_to("/Applications")
    dmg_path = DIST_DIR / "Transoria.dmg"
    tmp_dmg = DIST_DIR / "Transoria.tmp.dmg"
    for path in (dmg_path, tmp_dmg):
        if path.exists():
            path.unlink()
    _run(
        [
            "hdiutil",
            "create",
            "-volname",
            "Transoria",
            "-srcfolder",
            str(DMG_STAGING_DIR),
            "-ov",
            "-format",
            "UDZO",
            str(tmp_dmg),
        ],
        cwd=ROOT,
    )
    tmp_dmg.replace(dmg_path)
    return dmg_path


def _smoke_test_built_app(app_path: Path) -> None:
    executable = app_path / "Contents" / "MacOS" / "Transoria"
    if not executable.is_file():
        raise SystemExit(f"built app executable not found: {executable}")
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            str(executable),
            "--bridge-only",
            "--bridge-port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + SMOKE_TEST_TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout is not None else ""
                raise SystemExit(
                    "macOS app smoke test failed: app exited before bridge "
                    "became healthy.\n"
                    + output
                )
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health",
                    timeout=0.5,
                ) as response:
                    if response.status == 200:
                        print("[build] macOS app smoke test passed")
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        raise SystemExit(
            "macOS app smoke test timed out waiting for /api/health."
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("[build]", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


if __name__ == "__main__":
    main()
