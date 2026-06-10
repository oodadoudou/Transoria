from __future__ import annotations

import posixpath
from pathlib import Path
from urllib.parse import unquote
import zipfile
import xml.etree.ElementTree as ET


_MIMETYPE = "application/epub+zip"
_HTML_MEDIA_TYPES = {"application/xhtml+xml", "text/html"}
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"


def inspect_epub_structure(path: str | Path) -> dict[str, object]:
    archive_path = Path(path)
    warnings: list[str] = []
    missing_entries: list[str] = []
    counts = {"manifest": 0, "spine": 0, "html": 0, "nav": 0, "ncx": 0}
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
            opf_path = (rootfile.get("full-path") if rootfile is not None else "") or ""
            if not opf_path or opf_path not in names:
                missing_entries.append(opf_path or "content.opf")
                return _result(
                    "warning",
                    warnings,
                    missing_entries,
                    counts,
                    opf_path=opf_path,
                )

            opf_root = ET.fromstring(archive.read(opf_path))
            opf_dir = posixpath.dirname(opf_path)
            manifest_ids: set[str] = set()
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
                if media_type == _NCX_MEDIA_TYPE:
                    counts["ncx"] += 1
                if "nav" in properties:
                    counts["nav"] += 1
                if not href:
                    warnings.append(f"manifest item {item_id or '<missing id>'} has no href")
                    continue
                entry_name = _join_href(opf_dir, href)
                if entry_name not in names:
                    missing_entries.append(entry_name)

            for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
                counts["spine"] += 1
                idref = itemref.get("idref", "")
                if idref and idref not in manifest_ids:
                    warnings.append(f"spine itemref {idref} is not in manifest")

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


def _join_href(base_dir: str, href: str) -> str:
    clean_href = href.split("#", 1)[0].split("?", 1)[0]
    clean_href = unquote(clean_href)
    return posixpath.normpath(posixpath.join(base_dir, clean_href)).lstrip("./")


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
