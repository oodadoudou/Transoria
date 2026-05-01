"""Cross-platform path utilities.

Two filesystem quirks bite Korean / Chinese filenames:

1. **Windows MAX_PATH = 260**. Long absolute paths (anything past 260
   chars including the drive prefix) raise ``OSError [Errno 2]`` on
   open / mkdir / rename even when the file genuinely exists. The
   workaround is the ``\\?\`` long-path prefix, which routes the
   call through ``CreateFileW`` and bypasses the limit.

2. **macOS HFS+ / APFS NFD normalization**. The filesystem stores
   filenames as decomposed Unicode (e.g. ``ㅎ`` + combining marks
   instead of the precomposed syllable). Python ``Path`` keeps
   whatever form ``readdir`` returned, but ``Path("a") == Path("a-NFC")``
   compares strings and breaks for cache keys / set membership when
   one side came from disk and the other from settings JSON.

These helpers are no-ops on the platforms / paths that don't need
them, so callers can use them unconditionally.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path


_WINDOWS_LONG_PATH_PREFIX = "\\\\?\\"
# Slightly under MAX_PATH so we still catch paths that fit but are
# close to the limit when extended by relative-path joins downstream.
_WINDOWS_LONG_PATH_THRESHOLD = 240


def long_path(path: Path) -> Path:
    """Return ``path`` with the Windows ``\\?\`` long-path prefix
    when running on Windows and the absolute path is long enough to
    risk hitting the 260-char ``MAX_PATH`` limit. No-op everywhere
    else and on already-prefixed paths.
    """

    if sys.platform != "win32":
        return path
    text = str(path)
    if text.startswith(_WINDOWS_LONG_PATH_PREFIX):
        return path
    try:
        absolute = path.resolve(strict=False)
    except OSError:
        absolute = path
    absolute_text = str(absolute)
    if len(absolute_text) < _WINDOWS_LONG_PATH_THRESHOLD:
        return path
    # UNC paths use ``\\?\UNC\server\share\...`` instead of just
    # ``\\?\server\share\...`` so the prefix doesn't double the leading
    # backslashes.
    if absolute_text.startswith("\\\\"):
        return Path(_WINDOWS_LONG_PATH_PREFIX + "UNC\\" + absolute_text[2:])
    return Path(_WINDOWS_LONG_PATH_PREFIX + absolute_text)


def normalize_path_key(path: Path) -> str:
    """Return an NFC-normalized POSIX string for use as a dict / set
    key. Two ``Path`` objects pointing at the same file may compare
    unequal across the macOS readdir → NFD vs settings.json → NFC
    boundary; normalizing both sides through this helper before
    comparison fixes that mismatch without forcing a rename on disk.
    """

    return unicodedata.normalize("NFC", path.as_posix())


__all__ = ["long_path", "normalize_path_key"]
