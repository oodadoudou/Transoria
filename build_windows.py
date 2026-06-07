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
RESOURCE_DIR = ROOT / "transoria" / "resources"
DIST_DIR = ROOT / "dist" / "pyinstaller" / "windows"
WORK_DIR = ROOT / "build" / "pyinstaller" / "windows"
SPEC_DIR = ROOT / "build" / "pyinstaller" / "specs"
ICON_PATH = ROOT / "assets" / "icon.ico"
APP_DIR = DIST_DIR / "Transoria"

# Packages whose submodules PyInstaller's static analyzer misses
# (lazy/runtime imports). Without --collect-submodules they fail at
# first runtime use with ModuleNotFoundError.
SUBMODULE_PACKAGES = ("json_repair", "chardet", "lxml", "httpx")

# Verified before PyInstaller so a missing pip dep fails fast.
REQUIRED_RUNTIME_IMPORTS = (
    "json_repair",
    "chardet",
    "lxml",
    "httpx",
    "openpyxl",
    "webview",
)

# Microsoft's Edge WebView2 Evergreen Bootstrapper - bundled so the
# launcher can silently install WebView2 on machines that lack it.
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_BOOTSTRAPPER_NAME = "MicrosoftEdgeWebview2Setup.exe"

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
        "--make-zip",
        action="store_true",
        help="Also produce dist/Transoria-windows.zip from the onedir output.",
    )
    parser.add_argument(
        "--no-webview2-bootstrapper",
        action="store_true",
        help="Skip bundling the WebView2 Evergreen Bootstrapper.",
    )
    parser.add_argument(
        "--no-smoke-test",
        action="store_true",
        help="Skip the post-build smoke tests.",
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
    _refresh_egg_info()
    _note_unbundled_local_state()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    # --onedir + --collect-submodules; --onefile was unreliable because
    # PyInstaller's analyzer missed lazy submodules under the temp-extract
    # layout.
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
        f"{RESOURCE_DIR};transoria/resources",
        "--add-data",
        f"{ROOT / 'pyproject.toml'};.",
        # ``--collect-all`` (data + binaries + submodules + metadata) for
        # packages with lazy/runtime imports that PyInstaller's static
        # analyzer misses.
        "--collect-all",
        "webview",
        "--collect-data",
        "openpyxl",
        # Windows backend chain: pywebview -> winforms -> pythonnet ->
        # Python.Runtime.dll. ``edgechromium`` is a renderer module
        # *inside* winforms, not a standalone backend; passing
        # ``gui="edgechromium"`` to ``webview.start`` is a no-op.
        "--collect-all",
        "pythonnet",
        "--collect-all",
        "clr_loader",
        "--hidden-import",
        "webview.platforms.winforms",
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
    _write_distribution_artifacts()
    if not args.no_webview2_bootstrapper:
        _bundle_webview2_bootstrapper()
    _write_launch_bat()
    if not args.no_smoke_test:
        _smoke_test_built_exe()
    print(f"[build] Windows portable app: {APP_DIR}")
    if args.make_zip:
        zip_path = _create_release_zip()
        print(f"[build] Windows release zip: {zip_path}")
        print(
            "[build] distribute the ZIP — users extract the whole folder, "
            "double-click Launch_Transoria.bat (or Transoria.exe directly)."
        )
    else:
        print(
            "[build] zip step skipped — package "
            f"{APP_DIR} yourself with the release filename of your choice."
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


def _refresh_egg_info() -> None:
    """Regenerate transoria.egg-info to match current pyproject.toml.

    PyInstaller bundles egg-info into the exe; at runtime,
    importlib.metadata reads it before the bundled pyproject.toml.
    Stale egg-info from an earlier `pip install -e .` causes the exe
    to report the wrong version. --force-reinstall is required: pip
    skips re-running setuptools otherwise.
    """

    egg_info = ROOT / "transoria.egg-info"
    if egg_info.exists():
        shutil.rmtree(egg_info, ignore_errors=True)
    print("[build] regenerating transoria.egg-info to match pyproject.toml")
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--force-reinstall",
            "--quiet",
            "-e",
            str(ROOT),
        ],
        cwd=ROOT,
    )
    if not (egg_info / "PKG-INFO").exists():
        raise SystemExit(
            "egg-info regeneration failed: PKG-INFO not produced. "
            "Run `pip install -e .` manually to diagnose."
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
    import tomllib  # noqa: PLC0415

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
的位置。若安装目录不可写，程序会自动把用户数据改存到
%LOCALAPPDATA%\\Transoria，以避免设置保存失败。

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

默认情况下，所有设置、API Key、任务缓存都会写到 Transoria 文件夹里的：
  Transoria\\User Data\\

整个 Transoria 文件夹是自包含的：拷到 U 盘换台机器跑、备份整个文件
夹都能完整带走状态。

如果该目录不可写（例如放在 Program Files、只读目录，或被安全软件拦截），
Transoria 会自动改用：
  %LOCALAPPDATA%\\Transoria\\

⚠️ 注意：User Data 里包含明文 API Key。**分享或上传整个 Transoria
文件夹之前，请先删除 User Data 文件夹**，避免泄漏。如果程序已回退到
%LOCALAPPDATA%\\Transoria，也请一并检查该目录。

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
C:\\Windows, or the root of C:\\. If the install folder is not writable,
Transoria automatically stores user data in %LOCALAPPDATA%\\Transoria
instead so settings can still save.

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

By default, settings, API keys, and task caches live inside the Transoria
folder at:
  Transoria\\User Data\\

The whole Transoria folder is self-contained: copy it to a USB stick,
move it between machines, or back it up — your state moves with it.

If that folder is not writable (for example under Program Files, a
read-only folder, or blocked by security software), Transoria falls back to:
  %LOCALAPPDATA%\\Transoria\\

WARNING: User Data contains plaintext API keys. **Delete the User Data
folder before sharing or uploading the Transoria folder** to avoid
leaking your keys. If fallback was used, also check %LOCALAPPDATA%\\Transoria.

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
    arch = platform.machine() or "unknown"
    print(
        f"[build] host: Python {platform.python_version()} on "
        f"{platform.system()} {platform.release()} ({arch})"
    )


def _bundle_webview2_bootstrapper() -> None:
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
        if target.exists():
            target.unlink()
        return
    size = target.stat().st_size
    print(f"[build] bundled WebView2 bootstrapper ({size:,} bytes)")


_LAUNCH_BAT = """@echo off
setlocal EnableDelayedExpansion

REM Transoria portable launcher: unblock MotW, ensure WebView2,
REM launch Transoria.exe.

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
    echo [Transoria] Microsoft Edge WebView2 Runtime is missing - required to render the UI.
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
    bat_path = APP_DIR / "Launch_Transoria.bat"
    body = _LAUNCH_BAT.replace("__BOOTSTRAPPER__", WEBVIEW2_BOOTSTRAPPER_NAME)
    # ASCII first so cp936/cp1252 cmd.exe never sees a stray smart quote;
    # fall back to utf-8-sig (which cmd tolerates) if a non-ASCII char
    # slips into the template.
    try:
        bat_path.write_text(body, encoding="ascii", newline="\r\n")
    except UnicodeEncodeError as exc:
        print(
            f"[build] WARNING: launcher contains non-ASCII char ({exc!r}); "
            "writing as UTF-8 with BOM."
        )
        bat_path.write_text(body, encoding="utf-8-sig", newline="\r\n")
    print(f"[build] wrote launcher: {bat_path.name}")


def _smoke_test_built_exe() -> None:
    """Two-phase smoke: gui-imports (catches pythonnet/CLR wiring) +
    bridge-only (catches missing submodules in the full import graph)."""

    if sys.platform != "win32":
        print("[build] smoke test skipped: non-Windows host (CI dry-run mode)")
        return
    exe = APP_DIR / "Transoria.exe"
    if not exe.is_file():
        print("[build] smoke test skipped: exe not found")
        return

    _smoke_test_gui_imports(exe)
    _smoke_test_bridge_only(exe)


def _smoke_test_gui_imports(exe: Path) -> None:
    print(f"[build] smoke test 1/2: {exe.name} --check-gui-imports")
    result = subprocess.run(
        [str(exe), "--check-gui-imports"],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"[build] gui-imports smoke test FAILED (exit {result.returncode}).\n"
            "--- stdout ---\n"
            f"{result.stdout}"
            "--- stderr ---\n"
            f"{result.stderr}"
            "--- end ---\n"
            "Check: pythonnet installed in build env; "
            "webview.platforms.winforms in --hidden-import; "
            "pythonnet/clr_loader in --collect-all; "
            "Python.Runtime.dll present under _internal/pythonnet/runtime/."
        )
    print(f"[build] gui-imports OK ({(result.stdout or '').strip()})")


def _smoke_test_bridge_only(exe: Path) -> None:
    print(
        f"[build] smoke test 2/2: {exe.name} --bridge-only on port "
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
        # serve_forever blocks; "still alive after timeout" == success.
        try:
            stdout, _ = proc.communicate(timeout=SMOKE_TEST_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            print(
                f"[build] bridge-only OK (process alive after "
                f"{SMOKE_TEST_TIMEOUT_SECONDS}s)"
            )
            return
        raise SystemExit(
            f"[build] bridge-only smoke test FAILED: exe exited with code {proc.returncode}.\n"
            "--- captured output ---\n"
            f"{stdout}"
            "--- end output ---\n"
            "Check: missing module in SUBMODULE_PACKAGES or --hidden-import."
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        time.sleep(0.5)  # let the bound port release before next step


if __name__ == "__main__":
    main()
