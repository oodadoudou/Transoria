#!/usr/bin/env python3
"""Build the Windows Transoria portable app with PyInstaller."""

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
APP_DIR = DIST_DIR / "Transoria"

# Lazy-importing packages whose submodules PyInstaller's static analyzer
# cannot see (the public API hides them behind runtime imports). Without
# `--collect-submodules` the bundled exe boots and then dies with
# `ModuleNotFoundError` on the first call into the package.
SUBMODULE_PACKAGES = ("json_repair", "chardet", "lxml")

# Top-level packages required at runtime. Verified before invoking
# PyInstaller so a missing pip dep fails fast with a clear message
# instead of producing an exe that crashes at startup.
REQUIRED_RUNTIME_IMPORTS = (
    "json_repair",
    "chardet",
    "lxml",
    "httpx",
    "openpyxl",
    "webview",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Transoria portable app for Windows."
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Reuse existing frontend/dist instead of running npm run build.",
    )
    parser.add_argument(
        "--skip-zip",
        action="store_true",
        help="Build only the onedir output and skip the release ZIP.",
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
        _ensure_frontend_deps()
        _run([_npm(), "run", "build"], cwd=FRONTEND_DIR)
    _require_frontend_dist()
    _require_pyinstaller()
    _require_runtime_imports()
    _note_unbundled_local_state()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    # ``--onedir`` ships a folder containing Transoria.exe alongside its
    # Python runtime, native DLLs, and bundled data. The user gets the
    # whole thing as a ZIP (see ``_create_release_zip``). Onefile mode
    # was tried first but PyInstaller's static analyzer routinely missed
    # lazily-imported submodules under the temp-extract layout, so the
    # exe booted and crashed on first use. Onedir + ``--collect-submodules``
    # is the reliable shape.
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
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
    ]
    for package in SUBMODULE_PACKAGES:
        cmd.extend(["--collect-submodules", package])
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
    _write_first_run_readme()
    print(f"[build] Windows portable app: {APP_DIR}")
    if not args.skip_zip:
        zip_path = _create_release_zip()
        print(f"[build] Windows release zip: {zip_path}")
        print(
            "[build] distribute the ZIP — users extract the whole folder "
            "before running Transoria.exe."
        )


def _npm() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
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


def _write_first_run_readme() -> None:
    if not APP_DIR.is_dir():
        return
    note = APP_DIR / "READ_ME_FIRST.txt"
    # Mark-of-the-Web on a downloaded ZIP propagates to every extracted
    # file and can block DLL loading at launch. The note tells the user
    # how to clear it before the first run.
    note.write_text(
        "Transoria 首次运行说明\n"
        "========================\n\n"
        "如果此 ZIP 是从 GitHub Release 下载的，Windows 可能会给压缩包\n"
        "打上 Mark-of-the-Web 标记，导致解压后的 .exe / .dll 在启动时\n"
        "被系统拦截。请按以下步骤操作：\n\n"
        "1) 右键 ZIP 文件 → 属性 → 勾选「解除锁定」(Unblock) → 应用。\n"
        "2) 把 ZIP 解压到一个普通文件夹（不要在压缩包预览里直接运行）。\n"
        "3) 双击 Transoria.exe 启动。\n\n"
        "若仍报错，可在该目录下右键 → 属性 → 解除锁定，逐个解锁文件。\n",
        encoding="utf-8",
    )


def _create_release_zip() -> Path:
    if not APP_DIR.is_dir():
        raise SystemExit(f"app folder not found: {APP_DIR}")
    base = DIST_DIR / "Transoria-windows"
    archive = Path(
        shutil.make_archive(
            str(base),
            "zip",
            root_dir=str(DIST_DIR),
            base_dir="Transoria",
        )
    )
    return archive


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("[build]", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


if __name__ == "__main__":
    main()
