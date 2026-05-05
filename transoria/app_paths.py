"""Runtime paths shared by source and packaged builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Transoria"

# Folder name used inside the portable Windows distribution to hold all
# user state (settings, API keys, prompts, task cache). Sits next to
# Transoria.exe so the entire application folder is self-contained and
# can be moved between machines (USB stick, backup, etc.) without
# losing state.
PORTABLE_USER_DATA_DIR = "User Data"


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def default_cache_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1] / ".transoria-cache"
    # Windows packaged builds ship as a portable folder; keep all user
    # state next to the exe so moving the folder moves the state with
    # it. If that folder is not writable, fall back to LocalAppData so
    # settings can still save when the user extracts into a protected
    # directory or a security tool blocks writes next to the exe.
    # Other platforms still use the platform-standard location.
    if sys.platform == "win32":
        portable = Path(sys.executable).resolve().parent / PORTABLE_USER_DATA_DIR
        if _is_writable_cache_dir(portable):
            return portable
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return root / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


def _is_writable_cache_dir(path: Path) -> bool:
    probe = path / ".transoria-write-test"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


__all__ = [
    "APP_NAME",
    "PORTABLE_USER_DATA_DIR",
    "default_cache_root",
    "resource_root",
]
