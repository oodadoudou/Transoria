from __future__ import annotations

import io
import json
import os
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image


_EPUB_SUFFIX = ".epub"
_FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2", ".eot"}
_IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
}
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
_MIMETYPE = "application/epub+zip"


@dataclass(frozen=True)
class EpubCompressOptions:
    suffix: str = "_压缩"
    replace_original: bool = False
    preserve_first_cover: bool = False
    font_mode: str = "deduplicate"
    quality: int = 50
    max_size: int = 1200
    recursive: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "EpubCompressOptions":
        return cls(
            suffix=str(data.get("suffix", "_压缩")).strip(),
            replace_original=bool(data.get("replace_original", False)),
            preserve_first_cover=bool(data.get("preserve_first_cover", False)),
            font_mode=_font_mode(data.get("font_mode", "deduplicate")),
            quality=_clamp_int(data.get("quality"), default=50, low=1, high=95),
            max_size=_clamp_int(data.get("max_size"), default=1200, low=200, high=4000),
            recursive=bool(data.get("recursive", True)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "suffix": self.suffix,
            "replace_original": self.replace_original,
            "preserve_first_cover": self.preserve_first_cover,
            "font_mode": self.font_mode,
            "quality": self.quality,
            "max_size": self.max_size,
            "recursive": self.recursive,
        }


@dataclass(frozen=True)
class EpubCompressAction:
    id: str
    source_path: str
    output_path: str
    selected: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "selected": self.selected,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "EpubCompressAction":
        return cls(
            id=str(data.get("id", "")),
            source_path=str(data.get("source_path", "")),
            output_path=str(data.get("output_path", "")),
            selected=bool(data.get("selected", True)),
        )


@dataclass(frozen=True)
class EpubCompressPlan:
    input_path: Path
    mode: str
    actions: tuple[EpubCompressAction, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "mode": self.mode,
            "actions": [action.to_dict() for action in self.actions],
            "totals": {"epub_files": len(self.actions)},
        }


@dataclass(frozen=True)
class EpubCompressResult:
    action_id: str
    source_path: str
    output_path: str
    status: str
    original_size_bytes: int = 0
    output_size_bytes: int = 0
    fonts_removed: int = 0
    images_compressed: int = 0
    images_skipped: int = 0
    entries_written: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        saved = self.original_size_bytes - self.output_size_bytes
        saved_percent = (
            saved / self.original_size_bytes * 100
            if self.original_size_bytes > 0
            else 0.0
        )
        return {
            "action_id": self.action_id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "status": self.status,
            "original_size_bytes": self.original_size_bytes,
            "output_size_bytes": self.output_size_bytes,
            "saved_bytes": saved,
            "saved_percent": saved_percent,
            "fonts_removed": self.fonts_removed,
            "images_compressed": self.images_compressed,
            "images_skipped": self.images_skipped,
            "entries_written": self.entries_written,
            "error": self.error,
        }


def build_epub_compress_plan(
    input_path: Path, *, mode: str, options: EpubCompressOptions
) -> EpubCompressPlan:
    resolved = input_path.expanduser().resolve()
    if mode == "file":
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"EPUB file does not exist: {input_path}")
        if resolved.suffix.lower() != _EPUB_SUFFIX:
            raise ValueError(f"input file must be .epub: {input_path}")
        files = [resolved]
    elif mode == "folder":
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"input folder does not exist: {input_path}")
        iterator = resolved.rglob("*") if options.recursive else resolved.glob("*")
        suffix = _effective_output_suffix(options)
        files = sorted(
            path
            for path in iterator
            if path.is_file()
            and path.suffix.lower() == _EPUB_SUFFIX
            and (options.replace_original or suffix not in path.stem)
        )
    else:
        raise ValueError(f"unsupported EPUB compress mode: {mode!r}")
    actions = tuple(
        EpubCompressAction(
            id=f"epub-{index:04d}",
            source_path=str(path),
            output_path=str(_output_path_for(path, options)),
        )
        for index, path in enumerate(files)
    )
    return EpubCompressPlan(input_path=resolved, mode=mode, actions=actions)


def compress_epub_file(
    action: EpubCompressAction, options: EpubCompressOptions
) -> EpubCompressResult:
    source = Path(action.source_path).expanduser().resolve()
    output = Path(action.output_path).expanduser().resolve()
    tmp_output = output
    try:
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"source EPUB not found: {source}")
        if source.suffix.lower() != _EPUB_SUFFIX:
            raise ValueError(f"source is not an EPUB file: {source}")
        original_size = source.stat().st_size
        if options.replace_original:
            tmp_output = source.with_name(f".{source.name}.transoria-compress.tmp")
            output = source
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            tmp_output = _unique_output(output)

        stats = _compress_archive(
            source,
            tmp_output,
            options=options,
        )
        if options.replace_original:
            os.replace(tmp_output, source)
            output = source
        else:
            output = tmp_output
        return EpubCompressResult(
            action_id=action.id,
            source_path=str(source),
            output_path=str(output),
            status="compressed",
            original_size_bytes=original_size,
            output_size_bytes=output.stat().st_size,
            fonts_removed=stats["fonts_removed"],
            images_compressed=stats["images_compressed"],
            images_skipped=stats["images_skipped"],
            entries_written=stats["entries_written"],
        )
    except Exception as exc:  # noqa: BLE001
        if tmp_output != source and tmp_output.exists() and tmp_output.suffix == ".tmp":
            try:
                tmp_output.unlink()
            except OSError:
                pass
        return EpubCompressResult(
            action_id=action.id,
            source_path=str(source),
            output_path=str(output),
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


def build_epub_compress_report(
    *,
    task_id: str,
    input_path: Path,
    mode: str,
    generated_at: str,
    results: Iterable[EpubCompressResult],
) -> dict[str, object]:
    rows = [result.to_dict() for result in results]
    compressed = sum(1 for row in rows if row["status"] == "compressed")
    failed = sum(1 for row in rows if row["status"] == "failed")
    return {
        "task_id": task_id,
        "generated_at": generated_at,
        "input_path": str(input_path),
        "mode": mode,
        "totals": {
            "actions": len(rows),
            "compressed": compressed,
            "failed": failed,
            "original_size_bytes": sum(
                int(row["original_size_bytes"]) for row in rows
            ),
            "output_size_bytes": sum(int(row["output_size_bytes"]) for row in rows),
            "fonts_removed": sum(int(row["fonts_removed"]) for row in rows),
            "images_compressed": sum(
                int(row["images_compressed"]) for row in rows
            ),
            "images_skipped": sum(int(row["images_skipped"]) for row in rows),
        },
        "results": rows,
    }


def _compress_archive(
    source: Path, output: Path, *, options: EpubCompressOptions
) -> dict[str, int]:
    fonts_removed = 0
    images_compressed = 0
    images_skipped = 0
    entries_written = 0
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        if options.font_mode == "remove":
            font_map: dict[str, str] = {}
            fonts_removed = sum(1 for info in infos if not info.is_dir() and _is_font(info.filename))
        else:
            font_map, fonts_removed = _build_font_map(archive, infos)
        cover_name = _find_first_cover_name(infos) if options.preserve_first_cover else None
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w") as target:
            mimetype_data = archive.read("mimetype") if "mimetype" in names else _MIMETYPE.encode()
            target.writestr(
                _stored_info("mimetype"),
                mimetype_data.strip() or _MIMETYPE.encode(),
                compress_type=zipfile.ZIP_STORED,
            )
            entries_written += 1
            for info in infos:
                if info.filename == "mimetype" or info.is_dir():
                    continue
                data = archive.read(info.filename)
                if _is_font(info.filename):
                    if options.font_mode == "remove" or font_map.get(info.filename) != info.filename:
                        continue
                if info.filename.lower().endswith(".opf"):
                    if options.font_mode == "remove":
                        data = _remove_font_manifest_items(data)
                    elif font_map:
                        data = _rewrite_font_manifest_items(data, info.filename, font_map)
                if info.filename.lower().endswith(".css"):
                    if options.font_mode == "remove":
                        data = _remove_font_face_rules(data)
                    elif font_map:
                        data = _rewrite_css_font_urls(data, info.filename, font_map)
                if _is_image(info.filename):
                    if info.filename == cover_name:
                        images_skipped += 1
                    else:
                        compressed = _compress_image(data, info.filename, options)
                        if len(compressed) < len(data):
                            data = compressed
                            images_compressed += 1
                        else:
                            images_skipped += 1
                target.writestr(
                    _clone_info(info),
                    data,
                    compress_type=zipfile.ZIP_DEFLATED,
                )
                entries_written += 1
    _validate_epub(output)
    return {
        "fonts_removed": fonts_removed,
        "images_compressed": images_compressed,
        "images_skipped": images_skipped,
        "entries_written": entries_written,
    }


def _compress_image(
    data: bytes, filename: str, options: EpubCompressOptions
) -> bytes:
    if len(data) < 30_000:
        return data
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = (image.format or Path(filename).suffix.lstrip(".")).upper()
            image = _resize_if_needed(image, options.max_size)
            if fmt in {"JPG", "JPEG"}:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                return _save_image(image, "JPEG", quality=options.quality, optimize=True)
            if fmt == "PNG":
                return _compress_png(image, data)
            if fmt == "WEBP":
                kwargs = {"quality": max(1, min(95, options.quality)), "method": 6}
                return _save_image(image, "WEBP", **kwargs)
            if fmt == "GIF":
                if image.mode != "P":
                    image = image.convert("P", palette=Image.Palette.ADAPTIVE)
                return _save_image(image, "GIF", optimize=True)
            if fmt in {"TIFF", "TIF"}:
                return _save_image(image, "TIFF", compression="tiff_lzw")
    except Exception:
        return data
    return data


def _compress_png(image: Image.Image, original: bytes) -> bytes:
    candidates: list[bytes] = []
    has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    candidates.append(_save_image(image, "PNG", optimize=True, compress_level=9))
    if not has_alpha:
        rgb = image.convert("RGB") if image.mode != "RGB" else image
        candidates.append(_save_image(rgb, "PNG", optimize=True, compress_level=9))
        if rgb.width * rgb.height < 1_000_000:
            palette = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
            candidates.append(_save_image(palette, "PNG", optimize=True, compress_level=9))
    return min(candidates + [original], key=len)


def _resize_if_needed(image: Image.Image, max_size: int) -> Image.Image:
    largest = max(image.size)
    if largest <= max_size:
        return image
    ratio = max_size / largest
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _save_image(image: Image.Image, fmt: str, **kwargs: object) -> bytes:
    output = io.BytesIO()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def _build_font_map(
    archive: zipfile.ZipFile, infos: Iterable[zipfile.ZipInfo]
) -> tuple[dict[str, str], int]:
    by_hash: dict[str, str] = {}
    mapping: dict[str, str] = {}
    removed = 0
    for info in infos:
        if info.is_dir() or not _is_font(info.filename):
            continue
        data = archive.read(info.filename)
        signature = _md5(data)
        kept = by_hash.setdefault(signature, info.filename)
        mapping[info.filename] = kept
        if kept != info.filename:
            removed += 1
    return mapping, removed


def _rewrite_font_manifest_items(
    data: bytes, opf_filename: str, font_map: Mapping[str, str]
) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    manifest = root.find(".//{*}manifest")
    if manifest is None:
        return data
    changed = False
    opf_dir = _zip_dirname(opf_filename)
    for item in list(manifest):
        href = item.get("href", "")
        media_type = item.get("media-type", "")
        if _is_font(href) or media_type in _FONT_MEDIA_TYPES:
            resolved = _resolve_zip_href(opf_dir, href)
            kept = font_map.get(resolved)
            if kept and kept != resolved:
                item.set("href", _relative_zip_href(opf_dir, kept))
                changed = True
    if not changed:
        return data
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _remove_font_manifest_items(data: bytes) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    manifest = root.find(".//{*}manifest")
    if manifest is None:
        return data
    removed = False
    for item in list(manifest):
        href = item.get("href", "")
        media_type = item.get("media-type", "")
        if _is_font(href) or media_type in _FONT_MEDIA_TYPES:
            manifest.remove(item)
            removed = True
    if not removed:
        return data
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _rewrite_css_font_urls(
    data: bytes, css_filename: str, font_map: Mapping[str, str]
) -> bytes:
    text = _decode_text(data)
    css_dir = _zip_dirname(css_filename)
    changed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        quote = match.group(1) or ""
        url = match.group(2).strip()
        if url.startswith(("data:", "http://", "https://", "#")):
            return match.group(0)
        path, separator, fragment = url.partition("#")
        resolved = _resolve_zip_href(css_dir, path)
        kept = font_map.get(resolved)
        if not kept or kept == resolved:
            return match.group(0)
        changed = True
        next_url = _relative_zip_href(css_dir, kept)
        if separator:
            next_url = f"{next_url}#{fragment}"
        return f"url({quote}{next_url}{quote})"

    rewritten = re.sub(
        r"url\s*\(\s*([\"']?)([^\"')]+)\1\s*\)",
        replace,
        text,
        flags=re.IGNORECASE,
    )
    return rewritten.encode("utf-8") if changed else data


def _remove_font_face_rules(data: bytes) -> bytes:
    text = _decode_text(data)
    rewritten = re.sub(r"@font-face\s*\{[^{}]*\}", "", text, flags=re.IGNORECASE | re.DOTALL)
    return rewritten.encode("utf-8") if rewritten != text else data


def _find_first_cover_name(infos: Iterable[zipfile.ZipInfo]) -> str | None:
    for info in infos:
        lower = info.filename.lower()
        basename = Path(lower).name
        if (
            _is_image(info.filename)
            and ("cover" in basename or "front" in basename or "표지" in info.filename or "커버" in info.filename)
        ):
            return info.filename
    return None


def _decode_text(data: bytes) -> str:
    head = data[:200].decode("ascii", errors="ignore")
    match = re.search(r'encoding\s*=\s*["\']([^"\']+)["\']', head, re.IGNORECASE)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "utf-16", "euc-kr", "cp949", "latin-1"])
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _zip_dirname(filename: str) -> str:
    parent = posixpath.dirname(filename.replace("\\", "/"))
    return "" if parent == "." else parent


def _resolve_zip_href(base_dir: str, href: str) -> str:
    href = href.replace("\\", "/").split("?", 1)[0]
    if href.startswith("/"):
        return posixpath.normpath(href.lstrip("/"))
    return posixpath.normpath(posixpath.join(base_dir, href)).lstrip("./")


def _relative_zip_href(base_dir: str, target: str) -> str:
    if not base_dir:
        return target
    return posixpath.relpath(target, base_dir)


def _output_path_for(path: Path, options: EpubCompressOptions) -> Path:
    if options.replace_original:
        return path
    return path.with_name(f"{path.stem}{_effective_output_suffix(options)}{path.suffix}")


def _effective_output_suffix(options: EpubCompressOptions) -> str:
    return options.suffix.strip() or "_压缩"


def _unique_output(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _is_font(name: str) -> bool:
    return Path(name).suffix.lower() in _FONT_SUFFIXES


def _is_image(name: str) -> bool:
    return Path(name).suffix.lower() in _IMAGE_SUFFIXES


def _md5(data: bytes) -> str:
    import hashlib

    return hashlib.md5(data).hexdigest()


def _stored_info(filename: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename)
    info.compress_type = zipfile.ZIP_STORED
    return info


def _clone_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    clone.comment = info.comment
    clone.extra = info.extra
    clone.internal_attr = info.internal_attr
    clone.external_attr = info.external_attr
    clone.create_system = info.create_system
    clone.compress_type = zipfile.ZIP_DEFLATED
    return clone


def _validate_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if not names:
            raise ValueError("output EPUB is empty")
        if names[0] != "mimetype":
            raise ValueError("output EPUB mimetype must be the first entry")
        if archive.read("mimetype").strip() != _MIMETYPE.encode():
            raise ValueError("output EPUB mimetype is invalid")


def _clamp_int(value: object, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(high, max(low, parsed))


def _font_mode(value: object) -> str:
    mode = str(value or "deduplicate").strip().lower()
    return mode if mode in {"deduplicate", "remove"} else "deduplicate"


def report_to_json(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, indent=2)


__all__ = [
    "EpubCompressAction",
    "EpubCompressOptions",
    "EpubCompressPlan",
    "EpubCompressResult",
    "build_epub_compress_plan",
    "build_epub_compress_report",
    "compress_epub_file",
    "report_to_json",
]
