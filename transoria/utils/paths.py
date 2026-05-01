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


def describe_os_error(exc: OSError, *, action: str = "I/O") -> str:
    """Translate an ``OSError`` into a user-friendly one-liner.

    Default exception messages bury the actionable cause ("disk full"
    vs "no permission" vs "path doesn't exist") behind ``[Errno 28]``
    style preambles. Surface the cause first so the Run Error banner
    tells the user what to fix.

    ``action`` is the human-readable verb of what was being attempted
    (e.g. ``"write output"``, ``"read settings"``); defaults to a
    neutral ``"I/O"``.
    """

    import errno

    code = exc.errno
    path = getattr(exc, "filename", None) or getattr(exc, "filename2", None) or ""
    detail = f" ({path})" if path else ""
    if code == errno.ENOSPC:
        return f"disk is full — cannot {action}{detail}"
    if code == errno.EACCES:
        return f"permission denied — cannot {action}{detail}"
    if code == errno.EROFS:
        return f"target is on a read-only filesystem — cannot {action}{detail}"
    if code == errno.ENOENT:
        return f"path does not exist — cannot {action}{detail}"
    if code == errno.EEXIST:
        return f"path already exists — cannot {action}{detail}"
    if code == errno.EISDIR:
        return f"target is a directory — cannot {action}{detail}"
    return f"{action} failed: {exc}{detail}"


def normalize_path_key(path: Path) -> str:
    """Return an NFC-normalized POSIX string for use as a dict / set
    key. Two ``Path`` objects pointing at the same file may compare
    unequal across the macOS readdir → NFD vs settings.json → NFC
    boundary; normalizing both sides through this helper before
    comparison fixes that mismatch without forcing a rename on disk.
    """

    return unicodedata.normalize("NFC", path.as_posix())


__all__ = ["describe_os_error", "long_path", "normalize_path_key"]
