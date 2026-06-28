from __future__ import annotations

import posixpath
import unicodedata
import urllib.parse
import zipfile


def normalize_epub_path(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/"))


def decode_epub_href(href: str) -> str:
    return normalize_epub_path(urllib.parse.unquote(href))


def strip_href_suffix(href: str) -> str:
    return href.split("#", 1)[0].split("?", 1)[0]


def resolve_epub_href(base_dir: str, href: str) -> str:
    decoded = decode_epub_href(strip_href_suffix(href))
    if decoded.startswith("/"):
        return posixpath.normpath(decoded.lstrip("/"))
    joined = posixpath.normpath(posixpath.join(base_dir, decoded))
    return joined.lstrip("./")


def archive_lookup_candidates(path: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for candidate in (
        path,
        normalize_epub_path(path),
        decode_epub_href(path),
        resolve_epub_href("", path),
    ):
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def find_archive_entry_by_normalized_path(
    archive: zipfile.ZipFile,
    path: str,
) -> str:
    for candidate in archive_lookup_candidates(path):
        try:
            archive.getinfo(candidate)
        except KeyError:
            continue
        return candidate

    lookup_keys = {decode_epub_href(candidate) for candidate in archive_lookup_candidates(path)}
    matches = [
        name
        for name in archive.namelist()
        if decode_epub_href(name) in lookup_keys
    ]
    return matches[0] if len(matches) == 1 else ""
