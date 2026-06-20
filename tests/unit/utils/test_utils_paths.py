"""Tests for ``transoria.utils.paths`` cross-platform helpers."""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

from transoria.utils.paths import long_path, normalize_path_key


def test_long_path_is_noop_on_non_windows() -> None:
    short = Path("/tmp/short")
    long_dir = Path("/tmp/" + "a" * 300)
    assert long_path(short) == short
    assert long_path(long_dir) == long_dir


def test_long_path_prefixes_long_windows_paths(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    long = Path("C:/" + "x" * 280)
    result = long_path(long)
    assert str(result).startswith("\\\\?\\")


def test_long_path_skips_short_windows_paths(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    short = Path("C:/Users/me/file.txt")
    assert long_path(short) == short


def test_long_path_does_not_double_prefix(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    already = Path("\\\\?\\C:\\" + "x" * 280)
    assert long_path(already) == already


def test_normalize_path_key_returns_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "한국어")
    nfc = unicodedata.normalize("NFC", "한국어")
    assert nfd != nfc  # macOS readdir / settings.json mismatch case
    assert normalize_path_key(Path(nfd)) == normalize_path_key(Path(nfc))


def test_normalize_path_key_uses_posix_separators(tmp_path: Path) -> None:
    key = normalize_path_key(tmp_path / "a" / "b")
    assert "\\" not in key
