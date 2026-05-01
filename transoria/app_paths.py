"""Runtime paths shared by source and packaged builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Transoria"


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def default_cache_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1] / ".transoria-cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


__all__ = ["APP_NAME", "default_cache_root", "resource_root"]
