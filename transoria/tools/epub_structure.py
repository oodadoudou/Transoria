from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import urlsplit
import zipfile
import xml.etree.ElementTree as ET

from transoria.formats.epub_paths import (
    find_archive_entry_by_normalized_path,
    resolve_epub_href,
)


_MIMETYPE = "application/epub+zip"
_HTML_MEDIA_TYPES = {"application/xhtml+xml", "text/html"}
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
_FONT_MEDIA_TYPES = {
    "application/font-woff",
    "application/font-woff2",
    "application/vnd.ms-opentype",
    "application/x-font-ttf",
    "font/otf",
    "font/ttf",
    "font/woff",
    "font/woff2",
}
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_REFERENCE_ATTRIBUTES = ("href", "src", "poster")


def inspect_epub_structure(path: str | Path) -> dict[str, object]:
    archive_path = Path(path)
    warnings: list[str] = []
    missing_entries: list[str] = []
    counts = {
        "manifest": 0,
        "spine": 0,
        "html": 0,
        "body_documents": 0,
        "nav": 0,
        "nav_links": 0,
        "ncx": 0,
        "ncx_links": 0,
        "images": 0,
        "fonts": 0,
        "css": 0,
        "resources": 0,
        "references_checked": 0,
    }
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if not infos:
                return _result(
                    "failed",
                    warnings,
                    missing_entries,
                    counts,
                    error="empty EPUB archive",
                )
            if infos[0].filename != "mimetype":
                warnings.append("mimetype is not the first zip entry")
            else:
                if infos[0].compress_type != zipfile.ZIP_STORED:
                    warnings.append("mimetype entry is compressed")
                mimetype = archive.read("mimetype").decode("ascii", errors="ignore").strip()
                if mimetype != _MIMETYPE:
                    warnings.append("mimetype content is not application/epub+zip")

            if "META-INF/container.xml" not in names:
                missing_entries.append("META-INF/container.xml")
                return _result("warning", warnings, missing_entries, counts)

            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            raw_opf_path = (rootfile.get("full-path") if rootfile is not None else "") or ""
            opf_path = _join_href("", raw_opf_path) if raw_opf_path else ""
            opf_entry = _archive_entry(archive, opf_path)
            if not opf_path or not opf_entry:
                missing_entries.append(opf_path or "content.opf")
                return _result(
                    "warning",
                    warnings,
                    missing_entries,
                    counts,
                    opf_path=opf_path,
                )

            opf_root = ET.fromstring(archive.read(opf_entry))
            opf_dir = posixpath.dirname(opf_path)
            manifest_ids: set[str] = set()
            manifest_entries: dict[str, tuple[str, str, set[str]]] = {}
            for item in opf_root.findall(".//{*}manifest/{*}item"):
                counts["manifest"] += 1
                item_id = item.get("id", "")
                href = item.get("href", "")
                media_type = item.get("media-type", "")
                properties = set((item.get("properties", "") or "").split())
                if item_id:
                    manifest_ids.add(item_id)
                if media_type in _HTML_MEDIA_TYPES:
                    counts["html"] += 1
                elif media_type.startswith("image/"):
                    counts["images"] += 1
                elif media_type in _FONT_MEDIA_TYPES:
                    counts["fonts"] += 1
                elif media_type == "text/css":
                    counts["css"] += 1
                if media_type == _NCX_MEDIA_TYPE:
                    counts["ncx"] += 1
                if "nav" in properties:
                    counts["nav"] += 1
                if not href:
                    warnings.append(f"manifest item {item_id or '<missing id>'} has no href")
                    continue
                entry_name = _join_href(opf_dir, href)
                counts["resources"] += 1
                if item_id:
                    manifest_entries[item_id] = (entry_name, media_type, properties)
                if not _archive_entry(archive, entry_name):
                    missing_entries.append(entry_name)

            for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
                counts["spine"] += 1
                idref = itemref.get("idref", "")
                if idref and idref not in manifest_ids:
                    warnings.append(f"spine itemref {idref} is not in manifest")

            for entry_name, media_type, properties in manifest_entries.values():
                if not _archive_entry(archive, entry_name):
                    continue
                if media_type in _HTML_MEDIA_TYPES:
                    _inspect_html_entry(
                        archive,
                        entry_name,
                        names,
                        counts,
                        warnings,
                        missing_entries,
                        is_nav="nav" in properties,
                    )
                elif media_type == _NCX_MEDIA_TYPE:
                    _inspect_ncx_entry(
                        archive,
                        entry_name,
                        names,
                        counts,
                        warnings,
                        missing_entries,
                    )
                elif media_type == "text/css":
                    _inspect_css_entry(
                        archive,
                        entry_name,
                        names,
                        counts,
                        missing_entries,
                    )

            status = "ok" if not warnings and not missing_entries else "warning"
            return _result(
                status,
                warnings,
                sorted(set(missing_entries)),
                counts,
                opf_path=opf_path,
            )
    except Exception as exc:  # noqa: BLE001
        return _result(
            "failed",
            warnings,
            sorted(set(missing_entries)),
            counts,
            error=f"{type(exc).__name__}: {exc}",
        )


def compare_epub_structure_checks(
    before: dict[str, object],
    after: dict[str, object],
    *,
    preserve_counts: tuple[str, ...],
    expected_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    warnings: list[str] = []
    after_status = str(after.get("status", "failed"))
    if after_status == "failed":
        return {
            "status": "failed",
            "warnings": [str(after.get("error", "output EPUB validation failed"))],
            "before_counts": dict(before.get("counts", {})),
            "after_counts": dict(after.get("counts", {})),
        }

    before_counts = dict(before.get("counts", {}))
    after_counts = dict(after.get("counts", {}))
    for key in preserve_counts:
        before_value = int(before_counts.get(key, 0))
        after_value = int(after_counts.get(key, 0))
        if before_value != after_value:
            warnings.append(
                f"{key} count changed from {before_value} to {after_value}"
            )
    for key, expected in (expected_counts or {}).items():
        actual = int(after_counts.get(key, 0))
        if actual != expected:
            warnings.append(f"{key} count is {actual}; expected {expected}")
    warnings.extend(str(item) for item in after.get("warnings", []) if item)
    missing = [str(item) for item in after.get("missing_entries", []) if item]
    if missing:
        warnings.append(f"{len(missing)} internal references are missing")
    return {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
        "before_counts": before_counts,
        "after_counts": after_counts,
    }


def _join_href(base_dir: str, href: str) -> str:
    return resolve_epub_href(base_dir, href)


def _archive_entry(archive: zipfile.ZipFile, path: str) -> str:
    return find_archive_entry_by_normalized_path(archive, path) if path else ""


def _inspect_html_entry(
    archive: zipfile.ZipFile,
    entry_name: str,
    names: set[str],
    counts: dict[str, int],
    warnings: list[str],
    missing_entries: list[str],
    *,
    is_nav: bool,
) -> None:
    try:
        root = ET.fromstring(archive.read(_archive_entry(archive, entry_name)))
    except ET.ParseError as exc:
        warnings.append(f"cannot parse XHTML {entry_name}: {exc}")
        return
    if root.find(".//{*}body") is not None:
        counts["body_documents"] += 1
    base_dir = posixpath.dirname(entry_name)
    for element in root.iter():
        for attribute in _REFERENCE_ATTRIBUTES:
            href = element.get(attribute, "")
            if not href or _is_external_reference(href):
                continue
            counts["references_checked"] += 1
            if is_nav and attribute == "href" and element.tag.rsplit("}", 1)[-1] == "a":
                counts["nav_links"] += 1
            resolved = _join_href(base_dir, href)
            if resolved and not _archive_entry(archive, resolved):
                missing_entries.append(resolved)


def _inspect_ncx_entry(
    archive: zipfile.ZipFile,
    entry_name: str,
    names: set[str],
    counts: dict[str, int],
    warnings: list[str],
    missing_entries: list[str],
) -> None:
    try:
        root = ET.fromstring(archive.read(_archive_entry(archive, entry_name)))
    except ET.ParseError as exc:
        warnings.append(f"cannot parse NCX {entry_name}: {exc}")
        return
    base_dir = posixpath.dirname(entry_name)
    for content in root.findall(".//{*}content"):
        href = content.get("src", "")
        if not href or _is_external_reference(href):
            continue
        counts["ncx_links"] += 1
        counts["references_checked"] += 1
        resolved = _join_href(base_dir, href)
        if resolved and not _archive_entry(archive, resolved):
            missing_entries.append(resolved)


def _inspect_css_entry(
    archive: zipfile.ZipFile,
    entry_name: str,
    names: set[str],
    counts: dict[str, int],
    missing_entries: list[str],
) -> None:
    css = archive.read(_archive_entry(archive, entry_name)).decode("utf-8", errors="replace")
    base_dir = posixpath.dirname(entry_name)
    for match in _CSS_URL.finditer(css):
        href = match.group(2).strip()
        if not href or _is_external_reference(href):
            continue
        counts["references_checked"] += 1
        resolved = _join_href(base_dir, href)
        if resolved and not _archive_entry(archive, resolved):
            missing_entries.append(resolved)


def _is_external_reference(href: str) -> bool:
    stripped = href.strip()
    if not stripped or stripped.startswith("#"):
        return True
    parsed = urlsplit(stripped)
    return bool(parsed.scheme or parsed.netloc)


def _result(
    status: str,
    warnings: list[str],
    missing_entries: list[str],
    counts: dict[str, int],
    *,
    opf_path: str = "",
    error: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "warnings": list(warnings),
        "missing_entries": list(missing_entries),
        "counts": dict(counts),
    }
    if opf_path:
        payload["opf_path"] = opf_path
    if error:
        payload["error"] = error
    return payload
