#!/usr/bin/env python3
"""Build the Windows Transoria app with PyInstaller."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
DIST_DIR = ROOT / "dist" / "pyinstaller" / "windows"
WORK_DIR = ROOT / "build" / "pyinstaller" / "windows"
SPEC_DIR = ROOT / "build" / "pyinstaller" / "specs"
ICON_PATH = ROOT / "assets" / "icon.ico"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Transoria for Windows.")
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Reuse existing frontend/dist instead of running npm run build.",
    )
    parser.add_argument(
        "--allow-non-windows",
        action="store_true",
        help="Allow command construction on non-Windows hosts for CI validation.",
    )
    args = parser.parse_args()

    if sys.platform != "win32" and not args.allow_non_windows:
        raise SystemExit("Windows builds must run on Windows.")

    if not args.skip_frontend:
        _run([_npm(), "run", "build"], cwd=FRONTEND_DIR)
    _require_frontend_dist()
    _require_pyinstaller()
    _note_unbundled_local_state()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    # ``--onefile`` produces a single self-extracting Transoria.exe so
    # the user can drop the file anywhere and run it without keeping a
    # surrounding folder. Cold-start cost is one-time per launch
    # (~3-5s on typical SSDs) — acceptable for a translation app that
    # runs for minutes-to-hours per session.
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
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
        f"{FRONTEND_DIST};frontend/dist",
        "--add-data",
        f"{ROOT / 'pyproject.toml'};.",
        "--collect-data",
        "webview",
        "--collect-binaries",
        "webview",
        "--collect-data",
        "openpyxl",
        "--hidden-import",
        "webview.platforms.edgechromium",
        # Defensive: PyInstaller usually finds these via static analysis,
        # but explicit hidden imports survive analyzer drift across
        # PyInstaller / lxml / chardet upgrades.
        "--hidden-import",
        "lxml._elementpath",
        "--hidden-import",
        "lxml.etree",
        "--hidden-import",
        "chardet",
        "--hidden-import",
        "json_repair",
    ]
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
    exe_path = DIST_DIR / "Transoria.exe"
    print(f"[build] Windows portable executable: {exe_path}")
    print(
        "[build] distribute this single file — no installer, no "
        "surrounding folder required."
    )


def _npm() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise SystemExit("npm not found. Install Node.js on the build machine.")
    return npm


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


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("[build]", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


if __name__ == "__main__":
    main()
