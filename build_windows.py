#!/usr/bin/env python3
"""Build the Windows Transoria portable app with PyInstaller."""

from __future__ import annotations

import argparse
import platform
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
DIST_DIR = ROOT / "dist" / "pyinstaller" / "windows"
WORK_DIR = ROOT / "build" / "pyinstaller" / "windows"
SPEC_DIR = ROOT / "build" / "pyinstaller" / "specs"
ICON_PATH = ROOT / "assets" / "icon.ico"
APP_DIR = DIST_DIR / "Transoria"

# Lazy-importing packages whose submodules PyInstaller's static analyzer
# cannot see (the public API hides them behind runtime imports). Without
# `--collect-submodules` the bundled exe boots and then dies with
# `ModuleNotFoundError` on the first call into the package.
SUBMODULE_PACKAGES = ("json_repair", "chardet", "lxml", "httpx")

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

# Microsoft's Edge WebView2 Evergreen Bootstrapper — a ~150 KB stub that
# downloads + installs the actual runtime. We bundle this next to the
# exe so ``Launch_Transoria.bat`` can install WebView2 silently on Win10
# LTSC / Server SKUs / stripped images that don't ship it. The link
# below is Microsoft's permanent fwlink and is permitted to redistribute
# under the WebView2 Runtime distribution terms.
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_BOOTSTRAPPER_NAME = "MicrosoftEdgeWebview2Setup.exe"

# Smoke-test port. Picked high to avoid collision with services on
# the build machine. If this single port is already taken, the smoke
# test will fail loudly — pick a different number and rebuild.
SMOKE_TEST_BRIDGE_PORT = 64577
SMOKE_TEST_TIMEOUT_SECONDS = 8


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
        "--no-webview2-bootstrapper",
        action="store_true",
        help=(
            "Skip downloading + bundling Microsoft's WebView2 Evergreen "
            "Bootstrapper. By default the build downloads it (~150 KB) "
            "from go.microsoft.com so the launcher can install WebView2 "
            "on machines that don't already have it."
        ),
    )
    parser.add_argument(
        "--no-smoke-test",
        action="store_true",
        help=(
            "Skip the post-build smoke test that boots the exe in "
            "--bridge-only mode to verify all imports load cleanly."
        ),
    )
    parser.add_argument(
        "--allow-non-windows",
        action="store_true",
        help="Allow command construction on non-Windows hosts for CI validation.",
    )
    args = parser.parse_args()

    if sys.platform != "win32" and not args.allow_non_windows:
        raise SystemExit("Windows builds must run on Windows.")

    _print_platform_banner()
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
        # ``--collect-all webview`` is the union of ``--collect-data``,
        # ``--collect-binaries``, ``--collect-submodules`` and
        # ``--copy-metadata``. pywebview discovers its platform backends
        # by importing them lazily at runtime; without metadata + every
        # submodule, the exe boots into an empty window or raises
        # ``ImportError: cannot import name 'guilib'``.
        "--collect-all",
        "webview",
        "--collect-data",
        "openpyxl",
        # Edge Chromium is the primary backend on Windows; winforms is
        # kept as a defensive fallback so pywebview's platform-selection
        # logic still has somewhere to land if WebView2 is absent at
        # runtime (the launcher will normally install it first).
        "--hidden-import",
        "webview.platforms.edgechromium",
        "--hidden-import",
        "webview.platforms.winforms",
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
    _write_distribution_artifacts()
    if not args.no_webview2_bootstrapper:
        _bundle_webview2_bootstrapper()
    _write_launch_bat()
    if not args.no_smoke_test:
        _smoke_test_built_exe()
    print(f"[build] Windows portable app: {APP_DIR}")
    if not args.skip_zip:
        zip_path = _create_release_zip()
        print(f"[build] Windows release zip: {zip_path}")
        print(
            "[build] distribute the ZIP — users extract the whole folder, "
            "double-click Launch_Transoria.bat (or Transoria.exe directly)."
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


def _write_distribution_artifacts() -> None:
    """Drop bilingual READMEs, a VERSION marker, and the portable
    Input/Output folder pair next to ``Transoria.exe``."""

    if not APP_DIR.is_dir():
        return
    version = _read_project_version()
    (APP_DIR / "VERSION.txt").write_text(f"{version}\n", encoding="utf-8")
    (APP_DIR / "README_CN.txt").write_text(_README_CN, encoding="utf-8")
    (APP_DIR / "README_EN.txt").write_text(_README_EN, encoding="utf-8")
    input_dir = APP_DIR / "Input"
    output_dir = APP_DIR / "Output"
    input_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    (input_dir / "HOW_TO_USE.txt").write_text(_INPUT_HOW_TO, encoding="utf-8")
    (output_dir / "HOW_TO_USE.txt").write_text(_OUTPUT_HOW_TO, encoding="utf-8")


def _read_project_version() -> str:
    import tomllib  # noqa: PLC0415 — 3.11+ stdlib

    with (ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


_README_CN = """\
Transoria 启动说明（中文）
==========================

【一、推荐启动方式：双击 Launch_Transoria.bat】

我们在文件夹里放了一个一键启动脚本 Launch_Transoria.bat，它会自动
帮你做三件事，避免在干净 Windows 机器上首次启动时踩坑：

  1) 解除 Mark-of-the-Web 锁定（解决从 ZIP 解压后 _internal\\*.dll
     加载失败「找不到指定的模块」的问题）。
  2) 检测 Microsoft Edge WebView2 Runtime 是否安装；若缺失，自动
     调用同目录下的 MicrosoftEdgeWebview2Setup.exe 静默安装
     （Win10 LTSC / Server / 精简版镜像通常不自带 WebView2）。
  3) 启动 Transoria.exe。

如果你的 Windows 已经装好 WebView2，并且 ZIP 是右键属性「解除锁定」
之后再解压的，那么直接双击 Transoria.exe 也能正常打开。

请把 ZIP 解压到一个普通可写目录（如「文档」「桌面」或自建文件夹），
不要解压到 C:\\Program Files、C:\\Windows、C:\\ 根目录这类受系统保护
的位置；否则首次启动时 User Data 文件夹会因为权限不足写入失败。

【二、目录结构】

  Transoria/
    Launch_Transoria.bat              推荐入口（解锁 + 检测 WebView2 + 启动）
    Transoria.exe                     主程序
    MicrosoftEdgeWebview2Setup.exe    WebView2 离线安装引导（约 150 KB）
    README_CN.txt                     本文件
    README_EN.txt                     English version
    VERSION.txt                       当前版本号
    Input/                            示例输入目录（可用可不用）
    Output/                           示例输出目录（可用可不用）
    User Data/                        首次启动后自动生成；存放设置 / API Key / 任务缓存
    _internal/                        运行时依赖（请勿修改或删除）

【三、使用流程】

  1) 双击 Launch_Transoria.bat（推荐）或 Transoria.exe 启动应用。
  2) 在「模型管理」页面填好 LLM API Key。
  3) 在「翻译 / 术语提取 / 批量替换」对应页面里点「输入文件夹 / 输出
     文件夹」选择路径。可以选用同目录下提供的 Input/ 和 Output/，
     也可以选你电脑上的任意其他文件夹（推荐放在 Documents 之类的位置
     更便于管理）。
  4) 点开始即可。

  附：若你选了同目录下的 Input/，可以把 .epub 或 .txt 小说文件直接
     拖进去；翻译结果会出现在你指定的输出文件夹。

【四、用户数据存放位置（便携模式）】

所有设置、API Key、任务缓存都会写到 Transoria 文件夹里的：
  Transoria\\User Data\\

整个 Transoria 文件夹是自包含的：拷到 U 盘换台机器跑、备份整个文件
夹都能完整带走状态。

⚠️ 注意：User Data 里包含明文 API Key。**分享或上传整个 Transoria
文件夹之前，请先删除 User Data 文件夹**，避免泄漏。

【五、升级】

下载新版 ZIP，解压覆盖整个 Transoria 文件夹即可：

  - 你的 User Data 文件夹不会被新版 ZIP 触碰（设置 / Key / 缓存全部保留）。
  - 同目录下 Input / Output 里你自己放的文件也不会被删除。

如遇问题或想反馈，欢迎到 GitHub 提 issue：
  https://github.com/oodadoudou/Transoria
"""

_README_EN = """\
Transoria Quick Start (English)
================================

[1] Recommended: double-click Launch_Transoria.bat

The folder includes a one-shot launcher that takes care of the three
things that bite users on fresh Windows machines:

  1) Clears the Mark-of-the-Web on every file in the folder (without
     this, _internal\\*.dll fail to load with "module not found"
     after a ZIP extracted from the internet).
  2) Detects whether Microsoft Edge WebView2 Runtime is installed,
     and silently installs it from the bundled bootstrapper if not.
     Win10 LTSC / Server / minimal images often ship without WebView2.
  3) Launches Transoria.exe.

If your Windows already has WebView2 and you unblocked the ZIP before
extracting, double-clicking Transoria.exe directly also works.

Extract the ZIP to a regular writable folder (e.g. Documents, Desktop,
or a folder you create). Do not extract into C:\\Program Files,
C:\\Windows, or the root of C:\\ — those are system-protected and the
User Data folder will fail to write on first launch.

[2] Folder layout

  Transoria/
    Launch_Transoria.bat              Recommended entry (unblock + WebView2 + launch)
    Transoria.exe                     Main program
    MicrosoftEdgeWebview2Setup.exe    WebView2 offline bootstrapper (~150 KB)
    README_CN.txt                     Chinese version
    README_EN.txt                     This file
    VERSION.txt                       Current version
    Input/                            Example input folder (optional)
    Output/                           Example output folder (optional)
    User Data/                        Auto-created on first launch; settings, API keys, task cache
    _internal/                        Runtime dependencies (do not modify)

[3] Usage

  1) Double-click Launch_Transoria.bat (recommended) or Transoria.exe.
  2) On the Model page, configure your LLM API key.
  3) On the Translation / Glossary / Batch Replacement pages, click
     the input/output folder pickers. You can use the bundled Input/
     and Output/ folders here, or pick any folder on your machine
     (Documents is a common choice).
  4) Click Start.

  Tip: if you pick the bundled Input/, just drop your .epub or .txt
       files in there. Output goes to whatever output folder you chose.

[4] User data location (portable)

Settings, API keys, and task caches all live inside the Transoria
folder at:
  Transoria\\User Data\\

The whole Transoria folder is self-contained: copy it to a USB stick,
move it between machines, or back it up — your state moves with it.

WARNING: User Data contains plaintext API keys. **Delete the User Data
folder before sharing or uploading the Transoria folder** to avoid
leaking your keys.

[5] Upgrading

Download the new ZIP and extract over the existing Transoria folder:

  - Your User Data folder is untouched (settings, keys, cache preserved).
  - Files you placed in Input / Output are also preserved.

Issues or feedback are welcome at:
  https://github.com/oodadoudou/Transoria
"""

_INPUT_HOW_TO = """\
这是一个示例输入目录（可选使用）
================================
This is an example input folder (optional).

启动 Transoria 后，在对应页面挑选「输入文件夹」时，可以选这个目录，
也可以选你电脑上任何其他文件夹。如选了这里，把 .epub / .txt 小说文件
直接拖进来即可。

After launching Transoria, when picking the "Input folder" on a
task page, you can point at this directory or any other folder on
your machine. If you choose this one, just drop .epub / .txt novel
files in here.

支持格式 / Supported formats:
  - .epub  （结构会被保留 / structure preserved）
  - .txt   （自动识别常见编码 / common encodings auto-detected）
"""

_OUTPUT_HOW_TO = """\
这是一个示例输出目录（可选使用）
================================
This is an example output folder (optional).

跟 Input 同理：你可以在应用里挑这个目录作为输出，也可以挑任何别的
位置。任务完成后结果会写到你选定的输出目录里。

Same idea as Input: you can pick this directory as the output, or pick
any other location. When a task completes, results are written to the
output folder you chose.

升级时新版 ZIP 不会删除你这里的文件。
Files in this folder are preserved across upgrades.
"""


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


def _print_platform_banner() -> None:
    """Surface the build host's Python + arch up front so the operator
    notices x64 vs ARM64 mismatches before shipping a wrong-arch
    portable. PyInstaller produces an exe that matches the host's
    architecture; cross-arch builds need a separate machine."""

    arch = platform.machine() or "unknown"
    print(
        f"[build] host: Python {platform.python_version()} on "
        f"{platform.system()} {platform.release()} ({arch})"
    )
    print(
        f"[build] producing a {arch} Windows portable. "
        "Users on a different CPU architecture (e.g. ARM64) will need "
        "a build from a matching host."
    )


def _bundle_webview2_bootstrapper() -> None:
    """Download Microsoft's WebView2 Evergreen Bootstrapper (~150 KB)
    into the app folder. ``Launch_Transoria.bat`` runs it silently when
    the runtime is missing so the user never sees a blank window on
    Win10 LTSC / Server / minimal images that ship without WebView2.
    Network failures degrade gracefully — the launcher falls back to
    sending the user to Microsoft's download page."""

    target = APP_DIR / WEBVIEW2_BOOTSTRAPPER_NAME
    if target.exists() and target.stat().st_size > 0:
        print(f"[build] WebView2 bootstrapper already present: {target.name}")
        return
    print(f"[build] downloading WebView2 bootstrapper from {WEBVIEW2_BOOTSTRAPPER_URL}")
    try:
        urllib.request.urlretrieve(WEBVIEW2_BOOTSTRAPPER_URL, target)
    except (urllib.error.URLError, OSError) as exc:
        print(
            f"[build] WARNING: WebView2 bootstrapper download failed: {exc}\n"
            "        The launcher will fall back to opening the MS download page."
        )
        # Drop a stub the launcher can detect-as-missing reliably.
        if target.exists():
            target.unlink()
        return
    size = target.stat().st_size
    print(f"[build] bundled WebView2 bootstrapper ({size:,} bytes)")


_LAUNCH_BAT = """@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  Transoria portable launcher
REM
REM  Steps (in order):
REM    1) cd to the script's own directory so paths work no matter
REM       where the user dropped the folder.
REM    2) Unblock files via PowerShell. Windows tags everything
REM       extracted from an internet-downloaded ZIP with the
REM       Mark-of-the-Web. Without this, _internal\\python312.dll
REM       and friends refuse to load on first launch.
REM    3) Verify Microsoft Edge WebView2 Runtime is present. The
REM       UI won't render without it. Install from the bundled
REM       bootstrapper (preferred) or send the user to MS download.
REM    4) Launch Transoria.exe.
REM ============================================================

cd /d "%~dp0"

echo [Transoria] Preparing files (clearing Mark-of-the-Web)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

set "WEBVIEW2_OK="
for %%K in (
    "HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    "HKLM\\SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    "HKCU\\SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
) do (
    reg query %%K /v pv >nul 2>&1
    if !errorlevel! == 0 set "WEBVIEW2_OK=1"
)

if not defined WEBVIEW2_OK (
    echo [Transoria] Microsoft Edge WebView2 Runtime is missing — required to render the UI.
    if exist "%~dp0__BOOTSTRAPPER__" (
        echo [Transoria] Installing it from the bundled setup ^(may prompt for UAC^)...
        "%~dp0__BOOTSTRAPPER__" /silent /install
        if errorlevel 1 (
            echo [Transoria] WebView2 install failed.
            echo            Install manually from:
            echo            https://go.microsoft.com/fwlink/p/?LinkId=2124703
            pause
            exit /b 1
        )
    ) else (
        echo [Transoria] Opening the Microsoft download page in your browser...
        start "" "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
        echo [Transoria] After installing WebView2, run Launch_Transoria.bat again.
        pause
        exit /b 1
    )
)

start "" "%~dp0Transoria.exe"
endlocal
"""


def _write_launch_bat() -> None:
    """Drop ``Launch_Transoria.bat`` next to the exe. This is the
    recommended entry point for end users — it papers over the two
    most common "fresh Windows machine" failures (Mark-of-the-Web
    locking files, WebView2 runtime missing) before invoking the exe.
    Users who ignore it and double-click ``Transoria.exe`` directly
    will still work fine on most Win10/Win11 machines that already
    have WebView2 and unblocked files."""

    bat_path = APP_DIR / "Launch_Transoria.bat"
    body = _LAUNCH_BAT.replace("__BOOTSTRAPPER__", WEBVIEW2_BOOTSTRAPPER_NAME)
    # ``encoding="ascii"`` enforces that we never accidentally drop a
    # non-ASCII char into a .bat that runs under cp936/cp1252; cmd.exe
    # is famously brittle around codepage mismatches.
    bat_path.write_text(body, encoding="ascii", newline="\r\n")
    print(f"[build] wrote launcher: {bat_path.name}")


def _smoke_test_built_exe() -> None:
    """Boot the freshly-built exe in ``--bridge-only`` mode for a few
    seconds and verify it doesn't crash. Bridge-only loads the entire
    Python import graph (json_repair, chardet, lxml, httpx, openpyxl,
    transoria internals) without needing a GUI / WebView2 — perfect
    for catching ``ModuleNotFoundError`` and DLL-load failures *here*
    instead of in the user's hands."""

    if sys.platform != "win32":
        # ``--allow-non-windows`` is a CI / dry-run convenience; the
        # exe we just produced is a Windows PE binary and won't run
        # on the host kernel.
        print("[build] smoke test skipped: non-Windows host (CI dry-run mode)")
        return
    exe = APP_DIR / "Transoria.exe"
    if not exe.is_file():
        print("[build] smoke test skipped: exe not found")
        return
    print(
        f"[build] smoke test: launching {exe.name} --bridge-only on port "
        f"{SMOKE_TEST_BRIDGE_PORT}"
    )
    proc = subprocess.Popen(
        [str(exe), "--bridge-only", "--bridge-port", str(SMOKE_TEST_BRIDGE_PORT)],
        cwd=APP_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        # If the exe crashes due to missing imports / DLLs, it exits in
        # < 1s. If imports succeed, ``serve_forever`` blocks indefinitely
        # — we wait the timeout and treat "still alive" as success.
        try:
            stdout, _ = proc.communicate(timeout=SMOKE_TEST_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            print(
                f"[build] smoke test passed (process alive after "
                f"{SMOKE_TEST_TIMEOUT_SECONDS}s)"
            )
            return
        # Process exited within the timeout — that's a packaging bug.
        # Surface stdout/stderr verbatim; users will need to add the
        # missing module to SUBMODULE_PACKAGES or hidden imports.
        raise SystemExit(
            f"[build] smoke test FAILED: exe exited with code {proc.returncode}.\n"
            "--- captured output ---\n"
            f"{stdout}"
            "--- end output ---\n"
            "Common causes: missing hidden import, missing DLL, PyInstaller "
            "analyzer drift. Add the missing module to SUBMODULE_PACKAGES "
            "or to --hidden-import in the PyInstaller invocation."
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        # Give the OS a beat to release the bound port before any
        # follow-up step (e.g. ZIP creation walks the same folder).
        time.sleep(0.5)


if __name__ == "__main__":
    main()
