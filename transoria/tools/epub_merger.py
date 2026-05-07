from __future__ import annotations

import html
import io
import os
import re
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image


_EPUB_SUFFIX = ".epub"
_MIMETYPE = "application/epub+zip"
_FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2", ".eot"}
_FONT_MEDIA_TYPES = {
    "application/x-font-ttf",
    "application/vnd.ms-opentype",
    "application/font-woff",
    "application/font-woff2",
    "font/ttf",
    "font/otf",
    "font/woff",
    "font/woff2",
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_HTML_MEDIA_TYPES = {"application/xhtml+xml", "text/html"}
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
_NAV_PROPERTY = "nav"
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass(frozen=True)
class EpubMergeOptions:
    output_path: str = ""
    quality: int = 60
    max_size: int = 1600
    keep_original_images: bool = False
    smart_cover: bool = True
    recursive: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "EpubMergeOptions":
        return cls(
            output_path=str(data.get("output_path", "")),
            quality=_clamp_int(data.get("quality"), default=60, low=1, high=95),
            max_size=_clamp_int(data.get("max_size"), default=1600, low=200, high=4000),
            keep_original_images=bool(data.get("keep_original_images", False)),
            smart_cover=bool(data.get("smart_cover", True)),
            recursive=bool(data.get("recursive", True)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "output_path": self.output_path,
            "quality": self.quality,
            "max_size": self.max_size,
            "keep_original_images": self.keep_original_images,
            "smart_cover": self.smart_cover,
            "recursive": self.recursive,
        }


@dataclass(frozen=True)
class EpubMergeAction:
    id: str
    source_path: str
    order: int
    title_hint: str
    size_bytes: int
    selected: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_path": self.source_path,
            "order": self.order,
            "title_hint": self.title_hint,
            "size_bytes": self.size_bytes,
            "selected": self.selected,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "EpubMergeAction":
        return cls(
            id=str(data.get("id", "")),
            source_path=str(data.get("source_path", "")),
            order=int(data.get("order", 0)),
            title_hint=str(data.get("title_hint", "")),
            size_bytes=int(data.get("size_bytes", 0)),
            selected=bool(data.get("selected", True)),
        )


@dataclass(frozen=True)
class EpubMergePlan:
    input_dir: Path
    output_path: Path
    title: str
    actions: tuple[EpubMergeAction, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "input_dir": str(self.input_dir),
            "output_path": str(self.output_path),
            "title": self.title,
            "actions": [action.to_dict() for action in self.actions],
            "totals": {"epub_files": len(self.actions)},
        }


@dataclass(frozen=True)
class EpubMergeResult:
    action_id: str
    input_dir: str
    output_path: str
    status: str
    merged_files: int = 0
    skipped_files: int = 0
    chapters_written: int = 0
    resources_written: int = 0
    fonts_removed: int = 0
    images_written: int = 0
    images_deduplicated: int = 0
    images_compressed: int = 0
    output_size_bytes: int = 0
    processed_files: tuple[dict[str, object], ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "input_dir": self.input_dir,
            "output_path": self.output_path,
            "status": self.status,
            "merged_files": self.merged_files,
            "skipped_files": self.skipped_files,
            "chapters_written": self.chapters_written,
            "resources_written": self.resources_written,
            "fonts_removed": self.fonts_removed,
            "images_written": self.images_written,
            "images_deduplicated": self.images_deduplicated,
            "images_compressed": self.images_compressed,
            "output_size_bytes": self.output_size_bytes,
            "processed_files": list(self.processed_files),
            "error": self.error,
        }


@dataclass(frozen=True)
class _CopiedHtml:
    original_href: str
    new_href: str
    title: str
    is_cover: bool


@dataclass(frozen=True)
class _NavEntry:
    title: str
    href: str
    children: tuple["_NavEntry", ...] = ()


def build_epub_merge_plan(
    input_dir: Path, *, options: EpubMergeOptions
) -> EpubMergePlan:
    base = input_dir.expanduser().resolve()
    if not base.exists() or not base.is_dir():
        raise ValueError(f"input folder does not exist: {input_dir}")
    output_path = _resolve_output_path(base, options)
    iterator = base.rglob("*") if options.recursive else base.glob("*")
    files = sorted(
        (
            path
            for path in iterator
            if path.is_file()
            and path.suffix.lower() == _EPUB_SUFFIX
            and path.resolve() != output_path
        ),
        key=lambda path: _sort_key_korean(path.name),
    )
    title = _safe_filename(output_path.stem)
    actions = tuple(
        EpubMergeAction(
            id=f"epub-{index:04d}",
            source_path=str(path),
            order=index,
            title_hint=path.stem,
            size_bytes=path.stat().st_size,
        )
        for index, path in enumerate(files)
    )
    return EpubMergePlan(
        input_dir=base,
        output_path=output_path,
        title=title,
        actions=actions,
    )


def merge_epub_files(
    *,
    action_id: str,
    input_dir: Path,
    output_path: Path,
    actions: Iterable[EpubMergeAction],
    options: EpubMergeOptions,
) -> EpubMergeResult:
    selected = tuple(
        sorted(
            (action for action in actions if action.selected),
            key=lambda action: action.order,
        )
    )
    base = input_dir.expanduser().resolve()
    output = output_path.expanduser().resolve()
    tmp_output: Path | None = None
    try:
        if not selected:
            raise ValueError("at least one EPUB file is required")
        for action in selected:
            source = Path(action.source_path).expanduser().resolve()
            if not source.exists() or not source.is_file():
                raise FileNotFoundError(f"source EPUB not found: {source}")
            if source.suffix.lower() != _EPUB_SUFFIX:
                raise ValueError(f"source is not an EPUB file: {source}")
            if output == source:
                raise ValueError("output path cannot overwrite a selected input EPUB")

        merger = _EpubMerger(options)
        stats = merger.merge([Path(action.source_path) for action in selected], output)
        tmp_output = stats.pop("_tmp_output", None)
        return EpubMergeResult(
            action_id=action_id,
            input_dir=str(base),
            output_path=str(output),
            status="merged",
            output_size_bytes=output.stat().st_size,
            **stats,
        )
    except Exception as exc:  # noqa: BLE001
        if tmp_output is not None and tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        return EpubMergeResult(
            action_id=action_id,
            input_dir=str(base),
            output_path=str(output),
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


def build_epub_merge_report(
    *,
    task_id: str,
    input_dir: Path,
    generated_at: str,
    result: EpubMergeResult,
) -> dict[str, object]:
    row = result.to_dict()
    return {
        "task_id": task_id,
        "generated_at": generated_at,
        "input_dir": str(input_dir),
        "totals": {
            "actions": 1,
            "merged": 1 if result.status == "merged" else 0,
            "failed": 1 if result.status == "failed" else 0,
            "merged_files": result.merged_files,
            "skipped_files": result.skipped_files,
            "chapters_written": result.chapters_written,
            "resources_written": result.resources_written,
            "fonts_removed": result.fonts_removed,
            "images_written": result.images_written,
            "images_deduplicated": result.images_deduplicated,
            "images_compressed": result.images_compressed,
        },
        "result": row,
        "results": [row],
    }


class _EpubMerger:
    def __init__(self, options: EpubMergeOptions) -> None:
        self.options = options
        self.title = ""
        self.author = ""
        self.language = "ko"
        self.image_signatures: dict[str, str] = {}
        self.css_signatures: dict[str, str] = {}
        self.files: dict[str, bytes] = {}
        self.manifest: list[dict[str, str]] = []
        self.spine: list[str] = []
        self.nav: list[_NavEntry] = []
        self.cover_signatures: set[str] = set()
        self.cover_image_id = ""
        self.cover_page_href = ""
        self.stats = {
            "merged_files": 0,
            "skipped_files": 0,
            "chapters_written": 0,
            "resources_written": 0,
            "fonts_removed": 0,
            "images_written": 0,
            "images_deduplicated": 0,
            "images_compressed": 0,
            "processed_files": [],
        }

    def merge(self, sources: list[Path], output: Path) -> dict[str, object]:
        self.title = _safe_filename(output.stem)
        output.parent.mkdir(parents=True, exist_ok=True)
        for epub_index, source in enumerate(sources):
            self._merge_source(epub_index, source)
        if not self.spine:
            raise ValueError("no readable spine documents found in selected EPUB files")
        self._add_package_files()

        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}.",
            suffix=".tmp",
            dir=str(output.parent),
            delete=False,
        ) as handle:
            tmp_output = Path(handle.name)
        try:
            self._write_epub(tmp_output)
            _validate_epub(tmp_output)
            os.replace(tmp_output, output)
        finally:
            if tmp_output.exists():
                try:
                    tmp_output.unlink()
                except OSError:
                    pass
        return {
            **self.stats,
            "processed_files": tuple(self.stats["processed_files"]),
            "_tmp_output": tmp_output,
        }

    def _merge_source(self, epub_index: int, source: Path) -> None:
        file_stats = {
            "source_path": str(source),
            "title": source.stem,
            "status": "merged",
            "chapters": 0,
            "resources": 0,
            "fonts_removed": 0,
            "warnings": [],
        }
        try:
            with zipfile.ZipFile(source) as archive:
                names = set(archive.namelist())
                opf_path = _find_opf_path(archive)
                opf_dir = str(Path(opf_path).parent).replace(".", "")
                if opf_dir:
                    opf_dir = opf_dir.replace("\\", "/").strip("/")
                root = ET.fromstring(_read_text(archive, opf_path))
                self._set_metadata(root)
                book_title = _metadata_title(root) or source.stem
                file_stats["title"] = book_title
                manifest_items = _manifest_items(root)
                item_by_id = {item["id"]: item for item in manifest_items if item.get("id")}
                resource_map: dict[str, str] = {}
                html_href_map: dict[str, str] = {}
                copied_html: list[_CopiedHtml] = []

                cover_href = _find_cover_href(root, manifest_items)
                for item in manifest_items:
                    href = item.get("href", "")
                    media_type = item.get("media-type", "")
                    if not href or not media_type.startswith("image/"):
                        continue
                    entry_name = _join_href(opf_dir, href)
                    if entry_name not in names:
                        file_stats["warnings"].append(f"missing image: {href}")
                        continue
                    data = archive.read(entry_name)
                    is_cover = href == cover_href or _is_cover_image(item)
                    new_href, wrote, compressed = self._store_image(
                        epub_index=epub_index,
                        href=href,
                        opf_dir=opf_dir,
                        item=item,
                        data=data,
                        is_cover=is_cover,
                    )
                    resource_map[_normalize_path(href, opf_dir)] = new_href
                    if wrote:
                        file_stats["resources"] += 1
                    if compressed:
                        self.stats["images_compressed"] += 1

                for item in manifest_items:
                    href = item.get("href", "")
                    media_type = item.get("media-type", "")
                    if not href or media_type != "text/css":
                        continue
                    entry_name = _join_href(opf_dir, href)
                    if entry_name not in names:
                        file_stats["warnings"].append(f"missing css: {href}")
                        continue
                    css = _read_text(archive, entry_name)
                    css = _process_css(css, epub_index, href, opf_dir, resource_map)
                    new_href, wrote = self._store_css(epub_index, href, css)
                    resource_map[_normalize_path(href, opf_dir)] = new_href
                    if wrote:
                        file_stats["resources"] += 1

                spine_refs = [
                    itemref.get("idref", "")
                    for itemref in root.findall(".//{*}spine/{*}itemref")
                    if itemref.get("idref")
                ]
                for idref in spine_refs:
                    item = item_by_id.get(idref)
                    if not item:
                        file_stats["warnings"].append(f"missing spine item: {idref}")
                        continue
                    href = item.get("href", "")
                    media_type = item.get("media-type", "")
                    if media_type not in _HTML_MEDIA_TYPES:
                        continue
                    entry_name = _join_href(opf_dir, href)
                    if entry_name not in names:
                        file_stats["warnings"].append(f"missing html: {href}")
                        continue
                    html_text = _read_text(archive, entry_name)
                    if self.options.smart_cover and self._should_skip_duplicate_cover_page(
                        html_text, epub_index, opf_dir, href, resource_map
                    ):
                        continue
                    title = _extract_html_title(html_text) or Path(href).stem or "Chapter"
                    is_cover_page = _is_cover_page_href(href) or _is_cover_page_title(title)
                    safe_name = _html_output_name(
                        href=href,
                        title=title,
                        book_title=book_title,
                        index=sum(1 for item in copied_html if not item.is_cover) + 1,
                        is_cover=is_cover_page,
                    )
                    new_href = _unique_href(f"epub_{epub_index:03d}/{safe_name}", self.files.keys())
                    html_text = _process_html(
                        html_text,
                        epub_index=epub_index,
                        href=href,
                        opf_dir=opf_dir,
                        new_href=new_href,
                        resource_map=resource_map,
                    )
                    self.files[new_href] = html_text.encode("utf-8")
                    new_id = f"html_{epub_index:03d}_{len(self.spine):05d}"
                    self.manifest.append(
                        {
                            "id": new_id,
                            "href": new_href,
                            "media-type": "application/xhtml+xml",
                        }
                    )
                    self.spine.append(new_id)
                    if is_cover_page and not self.cover_page_href:
                        self.cover_page_href = new_href
                    normalized_href = _normalize_path(href, opf_dir)
                    html_href_map[normalized_href] = new_href
                    copied_html.append(
                        _CopiedHtml(
                            original_href=normalized_href,
                            new_href=new_href,
                            title=title,
                            is_cover=is_cover_page,
                        )
                    )
                    file_stats["chapters"] += 1
                    self.stats["chapters_written"] += 1

                if copied_html:
                    self.nav.append(
                        _build_book_nav_entry(
                            archive=archive,
                            manifest_items=manifest_items,
                            opf_dir=opf_dir,
                            book_title=book_title,
                            copied_html=copied_html,
                            html_href_map=html_href_map,
                        )
                    )

                for item in manifest_items:
                    href = item.get("href", "")
                    media_type = item.get("media-type", "")
                    if not href or media_type.startswith("image/") or media_type in {"text/css", _NCX_MEDIA_TYPE}:
                        continue
                    if media_type in _HTML_MEDIA_TYPES or _NAV_PROPERTY in item.get("properties", ""):
                        continue
                    if media_type in _FONT_MEDIA_TYPES or Path(href).suffix.lower() in _FONT_SUFFIXES:
                        self.stats["fonts_removed"] += 1
                        file_stats["fonts_removed"] += 1
                        continue
                    entry_name = _join_href(opf_dir, href)
                    if entry_name not in names:
                        file_stats["warnings"].append(f"missing resource: {href}")
                        continue
                    new_href = f"resources/{epub_index:03d}_{_safe_resource_name(Path(href).name)}"
                    self.files[new_href] = archive.read(entry_name)
                    resource_map[_normalize_path(href, opf_dir)] = new_href
                    self.manifest.append(
                        {"id": f"res_{epub_index:03d}_{len(self.manifest)}", "href": new_href, "media-type": media_type}
                    )
                    file_stats["resources"] += 1
        except Exception as exc:  # noqa: BLE001
            file_stats["status"] = "skipped"
            file_stats["warnings"].append(f"{type(exc).__name__}: {exc}")
            self.stats["skipped_files"] += 1
        else:
            self.stats["merged_files"] += 1
        self.stats["resources_written"] += int(file_stats["resources"])
        self.stats["processed_files"].append(file_stats)

    def _set_metadata(self, root: ET.Element) -> None:
        if not self.author:
            creator = root.find(".//{http://purl.org/dc/elements/1.1/}creator")
            if creator is not None and creator.text:
                self.author = creator.text.strip()
        if self.language == "ko":
            language = root.find(".//{http://purl.org/dc/elements/1.1/}language")
            if language is not None and language.text:
                self.language = language.text.strip() or self.language

    def _store_image(
        self,
        *,
        epub_index: int,
        href: str,
        opf_dir: str,
        item: Mapping[str, str],
        data: bytes,
        is_cover: bool,
    ) -> tuple[str, bool, bool]:
        signature = f"{uuid.uuid5(uuid.NAMESPACE_OID, str(len(data))).hex}:{_md5(data)}"
        if signature in self.image_signatures:
            self.stats["images_deduplicated"] += 1
            return self.image_signatures[signature], False, False
        stored = data
        compressed = False
        if not self.options.keep_original_images and Path(href).suffix.lower() in _IMAGE_SUFFIXES:
            next_data = _compress_image(data, quality=85 if is_cover else self.options.quality, max_size=3000 if is_cover else self.options.max_size)
            if len(next_data) < len(data):
                stored = next_data
                compressed = True
        name = _unique_href(
            f"images/{_safe_resource_name(Path(href).name)}",
            self.files.keys(),
        )
        self.files[name] = stored
        self.image_signatures[signature] = name
        manifest_item = {
            "id": f"img_{epub_index:03d}_{len(self.manifest)}",
            "href": name,
            "media-type": item.get("media-type", "image/jpeg"),
        }
        if is_cover:
            manifest_item["properties"] = "cover-image"
            if not self.cover_image_id:
                self.cover_image_id = manifest_item["id"]
        self.manifest.append(manifest_item)
        self.stats["images_written"] += 1
        return name, True, compressed

    def _store_css(self, epub_index: int, href: str, css: str) -> tuple[str, bool]:
        data = css.encode("utf-8")
        signature = _md5(data)
        if signature in self.css_signatures:
            return self.css_signatures[signature], False
        new_href = f"styles/{epub_index:03d}_{_safe_resource_name(Path(href).name)}"
        self.files[new_href] = data
        self.css_signatures[signature] = new_href
        self.manifest.append(
            {
                "id": f"css_{epub_index:03d}_{len(self.manifest)}",
                "href": new_href,
                "media-type": "text/css",
            }
        )
        return new_href, True

    def _should_skip_duplicate_cover_page(
        self,
        html_text: str,
        epub_index: int,
        opf_dir: str,
        href: str,
        resource_map: Mapping[str, str],
    ) -> bool:
        lower_name = Path(href).name.lower()
        if not lower_name.startswith(("cover", "titlepage", "표지", "커버")):
            return False
        src = _first_img_src(html_text)
        if not src:
            return False
        mapped = resource_map.get(_normalize_path(src, _join_href(opf_dir, str(Path(href).parent))))
        if not mapped:
            return False
        if mapped in self.cover_signatures:
            return True
        self.cover_signatures.add(mapped)
        return False

    def _add_package_files(self) -> None:
        self.manifest.append({"id": "nav", "href": "nav.xhtml", "media-type": "application/xhtml+xml", "properties": "nav"})
        self.manifest.append({"id": "ncx", "href": "toc.ncx", "media-type": _NCX_MEDIA_TYPE})
        self.files["content.opf"] = self._build_opf().encode("utf-8")
        self.files["nav.xhtml"] = self._build_nav().encode("utf-8")
        self.files["toc.ncx"] = self._build_ncx().encode("utf-8")
        self.files["META-INF/container.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>'
        ).encode("utf-8")

    def _build_opf(self) -> str:
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = _xml_escape(self.title)
        author = _xml_escape(self.author or "Unknown")
        language = _xml_escape(self.language or "ko")
        manifest = "\n".join(_manifest_xml(item) for item in self.manifest)
        spine = "\n".join(f'<itemref idref="{_xml_escape(idref)}"/>' for idref in self.spine)
        cover_meta = (
            f'\n<meta name="cover" content="{_xml_escape(self.cover_image_id)}"/>'
            if self.cover_image_id
            else ""
        )
        guide = (
            "\n<guide>\n"
            f'<reference type="cover" title="Cover" href="{_xml_escape(self.cover_page_href)}"/>\n'
            "</guide>"
            if self.cover_page_href
            else ""
        )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" unique-identifier="uid" version="3.0">
<metadata>
<dc:identifier id="uid">urn:uuid:{uuid.uuid4()}</dc:identifier>
<dc:title>{title}</dc:title>
<dc:language>{language}</dc:language>
<dc:creator>{author}</dc:creator>
<meta property="dcterms:modified">{modified}</meta>
{cover_meta}
</metadata>
<manifest>
{manifest}
</manifest>
<spine toc="ncx">
{spine}
</spine>
{guide}
</package>"""

    def _build_nav(self) -> str:
        rows = "".join(_nav_entry_xhtml(entry) for entry in self.nav)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{_xml_escape(self.language or "ko")}">
<head><title>Table of Contents</title><meta charset="utf-8"/></head>
<body><nav epub:type="toc" id="toc"><h1>Table of Contents</h1><ol>{rows}</ol></nav></body>
</html>"""

    def _build_ncx(self) -> str:
        play = 1
        rows = []
        for entry in self.nav:
            row, play = _nav_entry_ncx(entry, play)
            rows.append(row)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="urn:uuid:{uuid.uuid4()}"/></head>
<docTitle><text>{_xml_escape(self.title)}</text></docTitle>
<navMap>{''.join(rows)}</navMap>
</ncx>"""

    def _write_epub(self, output: Path) -> None:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                zipfile.ZipInfo("mimetype"),
                _MIMETYPE,
                compress_type=zipfile.ZIP_STORED,
            )
            for href in sorted(self.files):
                archive.writestr(href, self.files[href])


def _clamp_int(value: object, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _build_book_nav_entry(
    *,
    archive: zipfile.ZipFile,
    manifest_items: list[dict[str, str]],
    opf_dir: str,
    book_title: str,
    copied_html: list[_CopiedHtml],
    html_href_map: Mapping[str, str],
) -> _NavEntry:
    first_content = next((item for item in copied_html if not item.is_cover), copied_html[0])
    original_entries = _extract_original_nav_entries(
        archive=archive,
        manifest_items=manifest_items,
        opf_dir=opf_dir,
        html_href_map=html_href_map,
    )
    children = _clean_nav_entries(original_entries)
    if len(children) <= 1:
        children = _fallback_nav_children(book_title, copied_html)
    if len(children) <= 1:
        children = ()
    return _NavEntry(title=book_title, href=first_content.new_href, children=children)


def _extract_original_nav_entries(
    *,
    archive: zipfile.ZipFile,
    manifest_items: list[dict[str, str]],
    opf_dir: str,
    html_href_map: Mapping[str, str],
) -> tuple[_NavEntry, ...]:
    ncx_href = _find_manifest_href(manifest_items, media_type=_NCX_MEDIA_TYPE)
    if ncx_href:
        entries = _extract_ncx_entries(archive, opf_dir, ncx_href, html_href_map)
        if entries:
            return entries
    nav_href = _find_nav_href(manifest_items)
    if nav_href:
        return _extract_epub3_nav_entries(archive, opf_dir, nav_href, html_href_map)
    return ()


def _extract_ncx_entries(
    archive: zipfile.ZipFile,
    opf_dir: str,
    ncx_href: str,
    html_href_map: Mapping[str, str],
) -> tuple[_NavEntry, ...]:
    ncx_path = _join_href(opf_dir, ncx_href)
    if ncx_path not in archive.namelist():
        return ()
    try:
        root = ET.fromstring(_read_text(archive, ncx_path))
    except ET.ParseError:
        return ()
    base_dir = str(Path(ncx_path).parent).replace("\\", "/").strip(".")
    base_dir = "" if base_dir == "." else base_dir.strip("/")
    nav_map = root.find(".//{*}navMap")
    if nav_map is None:
        return ()
    return tuple(
        entry
        for navpoint in list(nav_map)
        if _local_name(navpoint.tag) == "navPoint"
        for entry in [_parse_ncx_navpoint(navpoint, base_dir, html_href_map)]
        if entry is not None
    )


def _parse_ncx_navpoint(
    node: ET.Element,
    base_dir: str,
    html_href_map: Mapping[str, str],
) -> _NavEntry | None:
    label = _clean_text(
        "".join(text for text in node.findall(".//{*}navLabel/{*}text") for text in [text.text or ""])
    )
    content = node.find("./{*}content")
    href = _map_nav_href(content.get("src", "") if content is not None else "", base_dir, html_href_map)
    children = tuple(
        child_entry
        for child in list(node)
        if _local_name(child.tag) == "navPoint"
        for child_entry in [_parse_ncx_navpoint(child, base_dir, html_href_map)]
        if child_entry is not None
    )
    if not href and children:
        href = children[0].href
    if not href:
        return None
    return _NavEntry(title=label or Path(href).stem, href=href, children=children)


def _extract_epub3_nav_entries(
    archive: zipfile.ZipFile,
    opf_dir: str,
    nav_href: str,
    html_href_map: Mapping[str, str],
) -> tuple[_NavEntry, ...]:
    nav_path = _join_href(opf_dir, nav_href)
    if nav_path not in archive.namelist():
        return ()
    try:
        root = ET.fromstring(_replace_named_entities(_read_text(archive, nav_path)))
    except ET.ParseError:
        return ()
    base_dir = str(Path(nav_path).parent).replace("\\", "/").strip(".")
    base_dir = "" if base_dir == "." else base_dir.strip("/")
    nav = _find_toc_nav(root)
    if nav is None:
        return ()
    ol = next((child for child in list(nav) if _local_name(child.tag) == "ol"), None)
    if ol is None:
        return ()
    return tuple(
        entry
        for li in list(ol)
        if _local_name(li.tag) == "li"
        for entry in [_parse_epub3_nav_li(li, base_dir, html_href_map)]
        if entry is not None
    )


def _parse_epub3_nav_li(
    node: ET.Element,
    base_dir: str,
    html_href_map: Mapping[str, str],
) -> _NavEntry | None:
    href = ""
    title = ""
    children: tuple[_NavEntry, ...] = ()
    for child in list(node):
        name = _local_name(child.tag)
        if name == "a" and not href:
            href = _map_nav_href(child.get("href", ""), base_dir, html_href_map)
            title = _clean_text("".join(child.itertext()))
        elif name == "span" and not title:
            title = _clean_text("".join(child.itertext()))
        elif name == "ol":
            children = tuple(
                entry
                for li in list(child)
                if _local_name(li.tag) == "li"
                for entry in [_parse_epub3_nav_li(li, base_dir, html_href_map)]
                if entry is not None
            )
    if not href and children:
        href = children[0].href
    if not href:
        return None
    return _NavEntry(title=title or Path(href).stem, href=href, children=children)


def _find_toc_nav(root: ET.Element) -> ET.Element | None:
    for node in root.iter():
        if _local_name(node.tag) != "nav":
            continue
        epub_type = node.get("{http://www.idpf.org/2007/ops}type", "") or node.get("epub:type", "")
        if "toc" in epub_type.split():
            return node
    return None


def _fallback_nav_children(book_title: str, copied_html: list[_CopiedHtml]) -> tuple[_NavEntry, ...]:
    content = [item for item in copied_html if not item.is_cover]
    if len(content) <= 1:
        return ()
    rows = []
    for index, item in enumerate(content, start=1):
        title = item.title if not _is_generic_nav_title(item.title) else f"{book_title} {index}"
        rows.append(_NavEntry(title=title, href=item.new_href))
    return tuple(rows)


def _clean_nav_entries(entries: Iterable[_NavEntry]) -> tuple[_NavEntry, ...]:
    rows: list[_NavEntry] = []
    for entry in entries:
        children = _clean_nav_entries(entry.children)
        if _is_cover_page_title(entry.title) or _is_generic_nav_title(entry.title):
            rows.extend(children)
        else:
            rows.append(_NavEntry(title=entry.title, href=entry.href, children=children))
    return tuple(rows)


def _map_nav_href(src: str, base_dir: str, html_href_map: Mapping[str, str]) -> str:
    if not src:
        return ""
    path, separator, fragment = src.partition("#")
    mapped = html_href_map.get(_normalize_path(path, base_dir))
    if not mapped:
        return ""
    return f"{mapped}{separator}{fragment}" if separator else mapped


def _metadata_title(root: ET.Element) -> str:
    title = root.find(".//{http://purl.org/dc/elements/1.1/}title")
    return _clean_text(title.text or "") if title is not None else ""


def _find_manifest_href(
    manifest_items: list[dict[str, str]],
    *,
    media_type: str,
) -> str:
    for item in manifest_items:
        if item.get("media-type") == media_type and item.get("href"):
            return item["href"]
    return ""


def _find_nav_href(manifest_items: list[dict[str, str]]) -> str:
    for item in manifest_items:
        if _NAV_PROPERTY in item.get("properties", "").split() and item.get("href"):
            return item["href"]
    return ""


def _nav_entry_xhtml(entry: _NavEntry) -> str:
    children = "".join(_nav_entry_xhtml(child) for child in entry.children)
    child_block = f"<ol>{children}</ol>" if children else ""
    return (
        f'<li><a href="{_xml_escape(entry.href)}">{_xml_escape(entry.title)}</a>'
        f"{child_block}</li>"
    )


def _nav_entry_ncx(entry: _NavEntry, play: int) -> tuple[str, int]:
    current = play
    play += 1
    children = []
    for child in entry.children:
        child_xml, play = _nav_entry_ncx(child, play)
        children.append(child_xml)
    xml = (
        f'<navPoint id="nav-{current}" playOrder="{current}">'
        f"<navLabel><text>{_xml_escape(entry.title)}</text></navLabel>"
        f'<content src="{_xml_escape(entry.href)}"/>'
        f"{''.join(children)}</navPoint>"
    )
    return xml, play


def _resolve_output_path(base: Path, options: EpubMergeOptions) -> Path:
    if options.output_path.strip():
        output = Path(options.output_path).expanduser().resolve()
        if output.suffix.lower() != _EPUB_SUFFIX:
            output = output.with_suffix(_EPUB_SUFFIX)
        return output
    return (base / "merged.epub").resolve()


def _sort_key_korean(filename: str) -> tuple[int, int, str]:
    name = Path(filename).stem
    is_extra = 1 if any(key in name.lower() for key in ("외전", "특별", "번외", "spinoff")) else 0
    match = re.search(r"(?:제\s*)?(\d+)\s*(?:화|장|회|권|부|편|탄)?", name)
    number = int(match.group(1)) if match else 999999
    return (is_extra, number, filename)


def _find_opf_path(archive: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(_read_text(archive, "META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is not None:
            full_path = rootfile.get("full-path", "")
            if full_path and full_path in archive.namelist():
                return full_path
    except Exception:  # noqa: BLE001
        pass
    for candidate in ("OEBPS/content.opf", "EPUB/content.opf", "content.opf"):
        if candidate in archive.namelist():
            return candidate
    for name in archive.namelist():
        if name.lower().endswith(".opf"):
            return name
    raise ValueError("OPF file not found")


def _read_text(archive: zipfile.ZipFile, name: str) -> str:
    raw = archive.read(name)
    head = raw[:300].decode("ascii", errors="ignore")
    match = re.search(r'encoding\s*=\s*["\']([^"\']+)["\']', head, re.IGNORECASE)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "utf-16", "euc-kr", "cp949", "latin-1"])
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _manifest_items(root: ET.Element) -> list[dict[str, str]]:
    rows = []
    for item in root.findall(".//{*}manifest/{*}item"):
        rows.append(
            {
                "id": item.get("id", ""),
                "href": item.get("href", ""),
                "media-type": item.get("media-type", ""),
                "properties": item.get("properties", ""),
            }
        )
    return rows


def _find_cover_href(root: ET.Element, manifest_items: list[dict[str, str]]) -> str:
    for item in manifest_items:
        if "cover-image" in item.get("properties", ""):
            return item.get("href", "")
    cover_meta = root.find(".//{*}meta[@name='cover']")
    if cover_meta is not None:
        cover_id = cover_meta.get("content", "")
        for item in manifest_items:
            if item.get("id") == cover_id:
                return item.get("href", "")
    return ""


def _is_cover_image(item: Mapping[str, str]) -> bool:
    href = item.get("href", "").lower()
    return (
        "cover-image" in item.get("properties", "")
        or Path(href).name.startswith(("cover", "front", "title", "표지", "커버"))
    )


def _process_css(
    css: str,
    epub_index: int,
    href: str,
    opf_dir: str,
    resource_map: Mapping[str, str],
) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    css = re.sub(r"@font-face\s*\{[^{}]*\}", "", css, flags=re.DOTALL | re.IGNORECASE)
    opened = css.count("{") - css.count("}")
    if opened > 0:
        css += "\n" + ("}" * opened)

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(1)
        if url.startswith(("data:", "http://", "https://")):
            return match.group(0)
        normalized = _normalize_path(url, _join_href(opf_dir, str(Path(href).parent)))
        mapped = resource_map.get(normalized)
        if not mapped:
            return match.group(0)
        return f'url("../{mapped}")'

    css = re.sub(r"url\s*\(\s*[\"']?([^\"'()]+?)[\"']?\s*\)", replace_url, css, flags=re.IGNORECASE)
    css = re.sub(r";{2,}", ";", css)
    return css


def _process_html(
    html_text: str,
    *,
    epub_index: int,
    href: str,
    opf_dir: str,
    new_href: str,
    resource_map: Mapping[str, str],
) -> str:
    html_text = _replace_named_entities(html_text)
    html_text = re.sub(r";{2,}", ";", html_text)
    base_dir = _join_href(opf_dir, str(Path(href).parent))
    new_dir = str(Path(new_href).parent).replace("\\", "/")

    def replace_attr(match: re.Match[str]) -> str:
        attr = match.group(1)
        quote = match.group(2)
        value = match.group(3)
        if value.startswith(("data:", "http://", "https://", "#", "mailto:")):
            return match.group(0)
        mapped = resource_map.get(_normalize_path(value, base_dir))
        if not mapped:
            return match.group(0)
        rel = os.path.relpath(mapped, new_dir).replace("\\", "/")
        return f"{attr}={quote}{rel}{quote}"

    html_text = re.sub(r"\b(src|href)=([\"'])([^\"']+)\2", replace_attr, html_text, flags=re.IGNORECASE)

    def replace_style_url(match: re.Match[str]) -> str:
        url = match.group(1)
        if url.startswith(("data:", "http://", "https://")):
            return match.group(0)
        mapped = resource_map.get(_normalize_path(url, base_dir))
        if not mapped:
            return match.group(0)
        rel = os.path.relpath(mapped, new_dir).replace("\\", "/")
        return f'url("{rel}")'

    return re.sub(r"url\s*\(\s*[\"']?([^\"'()]+?)[\"']?\s*\)", replace_style_url, html_text, flags=re.IGNORECASE)


def _replace_named_entities(text: str) -> str:
    predefined = {"amp", "lt", "gt", "quot", "apos", "nbsp"}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in predefined:
            return match.group(0)
        codepoint = html.entities.html5.get(f"{name};")
        return codepoint if isinstance(codepoint, str) else match.group(0)

    return re.sub(r"&([a-zA-Z][a-zA-Z0-9]+);", replace, text)


def _extract_html_title(html_text: str) -> str:
    for pattern in (
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
        r"<h2[^>]*>(.*?)</h2>",
    ):
        match = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
        if match:
            title = _clean_text(re.sub(r"<[^>]+>", "", match.group(1)))
            if title:
                return title
    return ""


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _is_cover_page_href(href: str) -> bool:
    name = Path(href).name.lower()
    return name.startswith(("cover", "titlepage", "title_page", "표지", "커버"))


def _is_cover_page_title(title: str) -> bool:
    normalized = _clean_text(title).lower()
    return normalized in {"cover", "cover page", "title page", "표지", "커버"}


def _is_generic_nav_title(title: str) -> bool:
    normalized = _clean_text(title).lower()
    if not normalized:
        return True
    if _is_cover_page_title(normalized):
        return True
    return bool(
        re.fullmatch(
            r"(section|chapter|file|page|part|untitled)[\s_-]*\d*",
            normalized,
        )
    )


def _html_output_name(
    *,
    href: str,
    title: str,
    book_title: str,
    index: int,
    is_cover: bool,
) -> str:
    suffix = Path(href).suffix or ".xhtml"
    if is_cover:
        return _safe_resource_name(Path(href).name or f"cover{suffix}")
    stem = Path(href).stem
    if _is_generic_nav_title(title) or _is_generic_nav_title(stem):
        base = _safe_resource_name(_safe_filename(book_title))
        return f"{base}{suffix}" if index <= 1 else f"{base}_{index}{suffix}"
    return _safe_resource_name(Path(href).name or f"{_safe_filename(title)}{suffix}")


def _first_img_src(html_text: str) -> str:
    match = re.search(r"<img[^>]*\bsrc=[\"']([^\"']+)[\"']", html_text, re.IGNORECASE)
    return match.group(1) if match else ""


def _compress_image(data: bytes, *, quality: int, max_size: int) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                image = image.resize(
                    (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            output = io.BytesIO()
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                image.save(output, format="PNG", optimize=True)
            else:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue()
    except Exception:  # noqa: BLE001
        return data


def _manifest_xml(item: Mapping[str, str]) -> str:
    props = item.get("properties", "")
    props_text = f' properties="{_xml_escape(props)}"' if props else ""
    return (
        f'<item id="{_xml_escape(item["id"])}" href="{_xml_escape(item["href"])}" '
        f'media-type="{_xml_escape(item["media-type"])}"{props_text}/>'
    )


def _join_href(base: str, href: str) -> str:
    if not base:
        return href.replace("\\", "/").strip("/")
    return f"{base.strip('/')}/{href.replace('\\', '/').strip('/')}".strip("/")


def _normalize_path(href: str, base_dir: str) -> str:
    href = href.split("#", 1)[0]
    if href.startswith("/"):
        href = href.lstrip("/")
    parts: list[str] = []
    if base_dir:
        parts.extend(
            part
            for part in base_dir.replace("\\", "/").split("/")
            if part and part != "."
        )
    for part in href.replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _unique_href(href: str, existing: Iterable[str]) -> str:
    used = set(existing)
    if href not in used:
        return href
    path = Path(href)
    parent = str(path.parent).replace("\\", "/")
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = f"{parent}/{stem}_{index}{suffix}" if parent != "." else f"{stem}_{index}{suffix}"
        if candidate not in used:
            return candidate
        index += 1


def _safe_resource_name(name: str) -> str:
    clean = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    return clean or f"resource-{uuid.uuid4().hex[:8]}"


def _safe_filename(name: str) -> str:
    clean = _INVALID_FILENAME_CHARS.sub("", name).strip(" .-_")
    return clean or "Merged EPUB"


def _xml_escape(text: object) -> str:
    return html.escape(str(text), quote=True)


def _md5(data: bytes) -> str:
    import hashlib

    return hashlib.md5(data).hexdigest()


def _validate_epub(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names or names[0] != "mimetype":
            raise ValueError("EPUB mimetype must be the first entry")
        if archive.read("mimetype").decode("ascii", errors="ignore") != _MIMETYPE:
            raise ValueError("invalid EPUB mimetype")
        if "content.opf" not in names or "nav.xhtml" not in names:
            raise ValueError("merged EPUB is missing package files")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
