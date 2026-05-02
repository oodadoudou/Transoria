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
    _write_distribution_artifacts()
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

【一、首次运行：解除 Mark-of-the-Web】

从 GitHub Release 下载的 ZIP 在 Windows 上会被打上来源标记，可能导致
启动时被系统拦截。请按以下任一种方式处理：

  1) 解压前：右键 ZIP 文件 → 属性 → 勾选「解除锁定」→ 应用 → 再解压。
  2) 解压后：在 Transoria 目录中点 Transoria.exe → 右键 → 属性 →
     若有「解除锁定」选项请勾选并应用。

请把 ZIP 解压到一个普通可写目录（如「文档」「桌面」或自建文件夹），
不要解压到 C:\\Program Files、C:\\Windows、C:\\ 根目录这类受系统保护
的位置；否则首次启动时 User Data 文件夹会因为权限不足写入失败。

【二、目录结构】

  Transoria/
    Transoria.exe          主程序，双击启动
    README_CN.txt          本文件
    README_EN.txt          English version
    VERSION.txt            当前版本号
    Input/                 示例输入目录（可用可不用）
    Output/                示例输出目录（可用可不用）
    User Data/             首次启动后自动生成；存放设置 / API Key / 任务缓存
    _internal/             运行时依赖（请勿修改或删除）

【三、使用流程】

  1) 双击 Transoria.exe 启动应用。
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

[1] First launch: clear the Mark-of-the-Web

A ZIP downloaded from GitHub Releases is tagged by Windows as
"from the Internet", which can block DLL loading on launch. Choose one:

  1) Before extracting: right-click the ZIP -> Properties ->
     check "Unblock" -> Apply -> then extract.
  2) After extracting: right-click Transoria.exe -> Properties ->
     check "Unblock" if shown.

Extract the ZIP to a regular writable folder (e.g. Documents, Desktop,
or a folder you create). Do not extract into C:\\Program Files,
C:\\Windows, or the root of C:\\ — those are system-protected and the
User Data folder will fail to write on first launch.

[2] Folder layout

  Transoria/
    Transoria.exe          Main program, double-click to launch
    README_CN.txt          Chinese version
    README_EN.txt          This file
    VERSION.txt            Current version
    Input/                 Example input folder (optional)
    Output/                Example output folder (optional)
    User Data/             Auto-created on first launch; settings, API keys, task cache
    _internal/             Runtime dependencies (do not modify)

[3] Usage

  1) Double-click Transoria.exe.
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


if __name__ == "__main__":
    main()
