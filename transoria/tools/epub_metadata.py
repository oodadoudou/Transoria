from __future__ import annotations

import copy
import io
import os
import posixpath
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from PIL import Image

from transoria.tools.epub_compressor import (
    EpubCompressAction,
    EpubCompressOptions,
    compress_epub_file,
)


CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"

ET.register_namespace("", OPF_NS)
ET.register_namespace("dc", DC_NS)


@dataclass(frozen=True)
class EpubMetadataInfo:
    input_path: Path
    package_path: str
    title: str
    authors: tuple[str, ...]
    cover_href: str
    cover_archive_path: str
    has_cover: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "package_path": self.package_path,
            "title": self.title,
            "authors": list(self.authors),
            "cover_href": self.cover_href,
            "cover_archive_path": self.cover_archive_path,
            "has_cover": self.has_cover,
        }


@dataclass(frozen=True)
class EpubMetadataApplyResult:
    input_path: Path
    output_path: Path
    title: str
    authors: tuple[str, ...]
    cover_updated: bool
    metadata_updated: bool
    compressed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "title": self.title,
            "authors": list(self.authors),
            "cover_updated": self.cover_updated,
            "metadata_updated": self.metadata_updated,
            "compressed": self.compressed,
        }


@dataclass(frozen=True)
class _CoverTarget:
    item: ET.Element | None
    href: str
    archive_path: str
    media_type: str


def read_epub_metadata(input_path: str | Path) -> EpubMetadataInfo:
    epub_path = _validate_epub_path(input_path)
    with zipfile.ZipFile(epub_path) as archive:
        package_path = _read_package_path(archive)
        root = _read_opf(archive, package_path)
        title = _first_text(root, f".//{{{DC_NS}}}title")
        authors = tuple(
            text
            for creator in root.findall(f".//{{{DC_NS}}}creator")
            if (text := (creator.text or "").strip())
        )
        cover = _find_cover(root, package_path)
    return EpubMetadataInfo(
        input_path=epub_path,
        package_path=package_path,
        title=title,
        authors=authors,
        cover_href=cover.href if cover else "",
        cover_archive_path=cover.archive_path if cover else "",
        has_cover=cover is not None,
    )


def apply_epub_metadata(
    input_path: str | Path,
    output_path: str | Path,
    *,
    title: str = "",
    author: str = "",
    cover_path: str = "",
    overwrite: bool = False,
    compress: bool = False,
) -> EpubMetadataApplyResult:
    epub_path = _validate_epub_path(input_path)
    out_path = Path(output_path).expanduser()
    if out_path.suffix.lower() != ".epub":
        raise ValueError("Output path must end with .epub.")
    same_path = epub_path.resolve() == out_path.resolve()
    if same_path and not overwrite:
        raise ValueError("Output path matches input path; confirm overwrite first.")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    next_title = title.strip()
    next_author = author.strip()
    cover_source = Path(cover_path).expanduser() if cover_path.strip() else None
    if cover_source is not None and not cover_source.is_file():
        raise FileNotFoundError(f"Cover image not found: {cover_source}")
    if not next_title and not next_author and cover_source is None:
        raise ValueError("Provide at least a title, author, or cover image.")

    temp_paths: list[Path] = []
    metadata_output = (
        _temporary_epub_path(out_path) if same_path or compress else out_path
    )
    if metadata_output != out_path:
        temp_paths.append(metadata_output)

    try:
        with zipfile.ZipFile(epub_path) as source:
            package_path = _read_package_path(source)
            root = _read_opf(source, package_path)
            metadata = _metadata_node(root)
            manifest = _manifest_node(root)
            cover = _find_cover(root, package_path)
            cover_bytes: bytes | None = None
            cover_archive_path = ""

            if next_title:
                _set_single_text(metadata, f"{{{DC_NS}}}title", next_title)
            if next_author:
                _set_creators(metadata, next_author)
            if cover_source is not None:
                cover, cover_bytes = _prepare_cover_update(
                    root,
                    manifest,
                    metadata,
                    package_path,
                    cover,
                    cover_source,
                )
                cover_archive_path = cover.archive_path

            opf_bytes = _serialize_opf(root)
            _copy_epub_with_replacements(
                source,
                metadata_output,
                replacements={
                    package_path: opf_bytes,
                    **({cover_archive_path: cover_bytes} if cover_bytes else {}),
                },
            )

        if compress:
            compressed_output = _temporary_epub_path(out_path)
            temp_paths.append(compressed_output)
            compress_result = compress_epub_file(
                EpubCompressAction(
                    id="epub-metadata-output",
                    source_path=str(metadata_output),
                    output_path=str(compressed_output),
                ),
                EpubCompressOptions(
                    suffix="",
                    replace_original=False,
                    preserve_first_cover=True,
                ),
            )
            if compress_result.status != "compressed":
                raise ValueError(f"EPUB compression failed: {compress_result.error}")
            os.replace(compress_result.output_path, out_path)
        elif metadata_output != out_path:
            os.replace(metadata_output, out_path)
    finally:
        for temp_path in temp_paths:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    updated = read_epub_metadata(out_path)
    return EpubMetadataApplyResult(
        input_path=epub_path,
        output_path=out_path,
        title=updated.title,
        authors=updated.authors,
        cover_updated=cover_source is not None,
        metadata_updated=bool(next_title or next_author),
        compressed=compress,
    )


def _temporary_epub_path(target: Path) -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}.transoria-metadata.",
        suffix=".epub",
        dir=str(target.parent),
        delete=False,
    )
    handle.close()
    temp_path = Path(handle.name)
    temp_path.unlink()
    return temp_path


def _validate_epub_path(path: str | Path) -> Path:
    epub_path = Path(path).expanduser()
    if not epub_path.is_file():
        raise FileNotFoundError(f"EPUB not found: {epub_path}")
    if epub_path.suffix.lower() != ".epub":
        raise ValueError("Input path must be an .epub file.")
    return epub_path


def _read_package_path(archive: zipfile.ZipFile) -> str:
    try:
        raw = archive.read("META-INF/container.xml")
    except KeyError as exc:
        raise ValueError("Invalid EPUB: missing META-INF/container.xml.") from exc
    root = ET.fromstring(raw)
    rootfile = root.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None:
        rootfile = root.find(".//rootfile")
    if rootfile is None:
        raise ValueError("Invalid EPUB: missing package path in container.xml.")
    package_path = rootfile.attrib.get("full-path", "").strip()
    if not package_path:
        raise ValueError("Invalid EPUB: empty package path in container.xml.")
    if package_path not in archive.namelist():
        raise ValueError(f"Invalid EPUB: package file not found: {package_path}")
    return package_path


def _read_opf(archive: zipfile.ZipFile, package_path: str) -> ET.Element:
    try:
        return ET.fromstring(archive.read(package_path))
    except ET.ParseError as exc:
        raise ValueError(f"Invalid EPUB package XML: {package_path}") from exc


def _metadata_node(root: ET.Element) -> ET.Element:
    metadata = root.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        metadata = root.find("metadata")
    if metadata is None:
        metadata = ET.SubElement(root, f"{{{OPF_NS}}}metadata")
    return metadata


def _manifest_node(root: ET.Element) -> ET.Element:
    manifest = root.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        manifest = root.find("manifest")
    if manifest is None:
        manifest = ET.SubElement(root, f"{{{OPF_NS}}}manifest")
    return manifest


def _first_text(root: ET.Element, path: str) -> str:
    node = root.find(path)
    return (node.text or "").strip() if node is not None else ""


def _iter_manifest_items(root: ET.Element) -> Iterable[ET.Element]:
    manifest = _manifest_node(root)
    yield from manifest.findall(f"{{{OPF_NS}}}item")
    yield from manifest.findall("item")


def _find_cover(root: ET.Element, package_path: str) -> _CoverTarget | None:
    for item in _iter_manifest_items(root):
        properties = set(item.attrib.get("properties", "").split())
        if "cover-image" in properties and item.attrib.get("href"):
            return _cover_from_item(item, package_path)

    metadata = _metadata_node(root)
    for meta in list(metadata):
        if meta.attrib.get("name") == "cover":
            cover_id = meta.attrib.get("content", "")
            for item in _iter_manifest_items(root):
                if item.attrib.get("id") == cover_id and item.attrib.get("href"):
                    return _cover_from_item(item, package_path)

    for item in _iter_manifest_items(root):
        media_type = item.attrib.get("media-type", "")
        text = f"{item.attrib.get('id', '')} {item.attrib.get('href', '')}".lower()
        if media_type.startswith("image/") and "cover" in text:
            return _cover_from_item(item, package_path)
    return None


def _cover_from_item(item: ET.Element, package_path: str) -> _CoverTarget:
    href = item.attrib.get("href", "")
    archive_path = _resolve_href(package_path, href)
    media_type = item.attrib.get("media-type") or _media_type_for_suffix(Path(href).suffix)
    return _CoverTarget(
        item=item,
        href=href,
        archive_path=archive_path,
        media_type=media_type,
    )


def _resolve_href(package_path: str, href: str) -> str:
    base = posixpath.dirname(package_path)
    return posixpath.normpath(posixpath.join(base, unquote(href))).lstrip("/")


def _relative_to_package(package_path: str, archive_path: str) -> str:
    base = posixpath.dirname(package_path)
    return posixpath.relpath(archive_path, base) if base else archive_path


def _set_single_text(parent: ET.Element, tag: str, value: str) -> None:
    existing = parent.findall(tag)
    if existing:
        first = existing[0]
        first.text = value
        for node in existing[1:]:
            parent.remove(node)
    else:
        ET.SubElement(parent, tag).text = value


def _set_creators(metadata: ET.Element, value: str) -> None:
    creators = metadata.findall(f"{{{DC_NS}}}creator")
    if creators:
        first = creators[0]
        first.text = value
        for node in creators[1:]:
            metadata.remove(node)
    else:
        creator = ET.SubElement(metadata, f"{{{DC_NS}}}creator")
        creator.text = value


def _prepare_cover_update(
    root: ET.Element,
    manifest: ET.Element,
    metadata: ET.Element,
    package_path: str,
    cover: _CoverTarget | None,
    cover_source: Path,
) -> tuple[_CoverTarget, bytes]:
    if cover is None:
        archive_path = _new_cover_archive_path(package_path, cover_source.suffix)
        href = _relative_to_package(package_path, archive_path)
        media_type = _media_type_for_suffix(cover_source.suffix)
        item = ET.SubElement(
            manifest,
            f"{{{OPF_NS}}}item",
            {
                "id": _unique_manifest_id(root, "transoria-cover-image"),
                "href": href,
                "media-type": media_type,
                "properties": "cover-image",
            },
        )
        _ensure_legacy_cover_meta(metadata, item.attrib["id"])
        cover = _CoverTarget(
            item=item,
            href=href,
            archive_path=archive_path,
            media_type=media_type,
        )
    else:
        assert cover.item is not None
        properties = set(cover.item.attrib.get("properties", "").split())
        properties.add("cover-image")
        cover.item.attrib["properties"] = " ".join(sorted(properties))
        if cover.item.attrib.get("id"):
            _ensure_legacy_cover_meta(metadata, cover.item.attrib["id"])

    return cover, _read_cover_bytes(cover_source, target_media_type=cover.media_type)


def _new_cover_archive_path(package_path: str, suffix: str) -> str:
    ext = suffix.lower() if suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
    base = posixpath.dirname(package_path)
    return posixpath.normpath(posixpath.join(base, "Images", f"transoria_cover{ext}")).lstrip("/")


def _unique_manifest_id(root: ET.Element, base: str) -> str:
    existing = {item.attrib.get("id", "") for item in _iter_manifest_items(root)}
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def _ensure_legacy_cover_meta(metadata: ET.Element, item_id: str) -> None:
    for meta in list(metadata):
        if meta.attrib.get("name") == "cover":
            meta.attrib["content"] = item_id
            return
    ET.SubElement(metadata, f"{{{OPF_NS}}}meta", {"name": "cover", "content": item_id})


def _read_cover_bytes(path: Path, *, target_media_type: str) -> bytes:
    source_media_type = _media_type_for_suffix(path.suffix)
    raw = path.read_bytes()
    if source_media_type == target_media_type:
        return raw
    fmt = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }.get(target_media_type)
    if fmt is None:
        return raw
    with Image.open(io.BytesIO(raw)) as image:
        if fmt == "JPEG" and image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format=fmt)
        return output.getvalue()


def _media_type_for_suffix(suffix: str) -> str:
    match suffix.lower():
        case ".jpg" | ".jpeg":
            return "image/jpeg"
        case ".png":
            return "image/png"
        case ".webp":
            return "image/webp"
        case ".gif":
            return "image/gif"
        case _:
            return "application/octet-stream"


def _serialize_opf(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _copy_epub_with_replacements(
    source: zipfile.ZipFile,
    output_path: Path,
    *,
    replacements: dict[str, bytes | None],
) -> None:
    with zipfile.ZipFile(output_path, "w") as target:
        written: set[str] = set()
        for info in source.infolist():
            if info.filename in written:
                continue
            data = replacements.get(info.filename)
            if data is None:
                data = source.read(info.filename)
            next_info = copy.copy(info)
            target.writestr(next_info, data)
            written.add(info.filename)
        for filename, data in replacements.items():
            if data is not None and filename not in written:
                target.writestr(filename, data)


__all__ = [
    "EpubMetadataApplyResult",
    "EpubMetadataInfo",
    "apply_epub_metadata",
    "read_epub_metadata",
]
