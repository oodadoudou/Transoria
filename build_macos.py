#!/usr/bin/env python3
"""Build the macOS Transoria app with PyInstaller."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
DIST_DIR = ROOT / "dist" / "pyinstaller" / "macos"
WORK_DIR = ROOT / "build" / "pyinstaller" / "macos"
SPEC_DIR = ROOT / "build" / "pyinstaller" / "specs"


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
    args = parser.parse_args()

    if sys.platform != "darwin" and not args.allow_non_macos:
        raise SystemExit("macOS builds must run on macOS.")

    if not args.skip_frontend:
        _run([_npm(), "run", "build"], cwd=FRONTEND_DIR)
    _require_frontend_dist()
    _require_pyinstaller()
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
        "--hidden-import",
        "webview.platforms.cocoa",
        str(ROOT / "app.py"),
    ]
    _run(cmd, cwd=ROOT)
    _verify_spec_excludes_local_state()
    print(f"[build] macOS app: {DIST_DIR / 'Transoria.app'}")


def _npm() -> str:
    npm = shutil.which("npm")
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
