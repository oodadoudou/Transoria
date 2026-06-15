from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import html.entities
import posixpath
import re
from pathlib import Path
from typing import Iterator
import unicodedata
import zipfile

from lxml import etree


OCF_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:container"
ROW_BASE_NAV = 8_000_000_000
ROW_BASE_NCX = 9_000_000_000
ROW_MULTIPLIER = 1_000_000
HTML_DOCUMENT_SUFFIXES = (".xhtml", ".xhtm", ".html", ".htm")

BLOCK_TAGS = {
    # Standard text blocks.
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "div",
    "li",
    "td",
    "th",
    "caption",
    "figcaption",
    "dt",
    "dd",
    # Sectioning + flow-content containers. Many novel EPUBs put prose,
    # quoted passages, asides, and footnotes directly inside these tags
    # without a wrapping <p>. Including them here ensures the text is
    # extracted (and thus reachable for translation and glossary scans).
    "blockquote",
    "aside",
    "address",
    "section",
    "article",
    "header",
    "footer",
    "main",
    "nav",
    "figure",
    "details",
    "summary",
    "hgroup",
    "body",
}
SKIP_SUBTREE_TAGS = {
    "script",
    "style",
    "code",
    "pre",
    "kbd",
    "samp",
    "var",
    "noscript",
    "rt",
    "rp",
}

RE_SLOT_INLINE_WHITESPACE = re.compile(r"[\r\n\t]+")
RE_MULTI_SPACE = re.compile(r"[ ]{2,}")
RE_HTML_NAMED_ENTITY = re.compile(rb"&([A-Za-z][A-Za-z0-9._:-]*);")
RE_CDATA_SECTION = re.compile(rb"<!\[CDATA\[.*?\]\]>", re.DOTALL)
RE_NCX_BARE_AMP = re.compile(
    rb"&(?!(?:[A-Za-z][A-Za-z0-9._:-]*|#[0-9]+|#[xX][0-9A-Fa-f]+);)"
)
VOID_TAG_PATTERN = (
    rb"area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr"
)
RE_REDUNDANT_VOID_END_TAG = re.compile(
    rb"(<(?P<tag>" + VOID_TAG_PATTERN + rb")\b[^>]*?/>)\s*</(?P=tag)\s*>",
    re.IGNORECASE,
)
RE_EMPTY_VOID_PAIR = re.compile(
    rb"<(?P<tag>"
    + VOID_TAG_PATTERN
    + rb")(?P<attrs>\b[^>/]*?)>\s*</(?P=tag)\s*>",
    re.IGNORECASE,
)


class EpubTextKind(str, Enum):
    BODY = "body"
    NAV = "nav"
    NCX = "ncx"


@dataclass(frozen=True)
class EpubPartRef:
    slot: str
    path: str


@dataclass(frozen=True)
class EpubPackageInfo:
    opf_path: str
    opf_dir: str
    opf_version_major: int
    spine_paths: list[str]
    nav_path: str | None
    ncx_path: str | None


@dataclass(frozen=True)
class EpubTextSegment:
    index: int
    doc_path: str
    block_path: str
    text: str
    source_digest: str
    kind: EpubTextKind
    parts: list[EpubPartRef]
    row: int


@dataclass(frozen=True)
class EpubDocument:
    path: Path
    package: EpubPackageInfo
    segments: list[EpubTextSegment]
    archive_bytes: bytes | None = None


_MAX_EPUB_BYTES: int = 500 * 1024 * 1024  # 500 MB hard cap


def parse_epub_file(path: Path, *, buffer_archive: bool = False) -> EpubDocument:
    """Parse an EPUB file.

    When ``buffer_archive`` is true the full archive bytes are kept on the
    returned :class:`EpubDocument`. The writer uses the buffer in preference
    to re-reading from disk, so writeback survives the source file being
    moved or deleted between parse and write — an important guarantee for
    long-running translation tasks.

    Files larger than ``_MAX_EPUB_BYTES`` (500 MB) are rejected with a
    typed ``ValueError`` rather than blindly loaded. With ``buffer_archive``
    true that prevents an OOM kill on the parent process; even with the
    flag false the in-memory data structures alongside ``archive_bytes``
    grow with file size, so a hard ceiling protects the desktop session.
    """

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"Cannot stat EPUB file: {path}") from exc
    if size > _MAX_EPUB_BYTES:
        raise ValueError(
            f"EPUB file is too large to load safely "
            f"({size / 1024 / 1024:.1f} MB > "
            f"{_MAX_EPUB_BYTES / 1024 / 1024:.0f} MB cap): {path}"
        )

    try:
        raw = path.read_bytes() if buffer_archive else None
    except FileNotFoundError as exc:
        raise ValueError(f"Invalid EPUB archive: {path}") from exc

    try:
        if raw is not None:
            import io
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                package = parse_package(archive)
                segments = extract_segments(archive, package)
        else:
            with zipfile.ZipFile(path) as archive:
                package = parse_package(archive)
                segments = extract_segments(archive, package)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid EPUB archive: {path}") from exc
    except KeyError as exc:
        raise ValueError(f"Invalid EPUB structure: missing {exc.args[0]}") from exc
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Invalid EPUB XML: {path}") from exc

    return EpubDocument(
        path=path, package=package, segments=segments, archive_bytes=raw
    )


def parse_package(archive: zipfile.ZipFile) -> EpubPackageInfo:
    opf_path = parse_container_opf_path(archive)
    opf_root = parse_epub_xml(read_archive_entry(archive, opf_path))
    opf_version = opf_root.get("version") or "2.0"
    try:
        opf_version_major = int(opf_version.split(".", 1)[0])
    except ValueError:
        opf_version_major = 2

    opf_dir = posixpath.dirname(opf_path)
    manifest = parse_manifest(opf_root, opf_dir)
    spine_paths = parse_spine_paths(opf_root, manifest)
    nav_path = find_nav_path(manifest)
    ncx_path = find_ncx_path(opf_root, manifest)

    return EpubPackageInfo(
        opf_path=opf_path,
        opf_dir=opf_dir,
        opf_version_major=opf_version_major,
        spine_paths=spine_paths,
        nav_path=nav_path,
        ncx_path=ncx_path,
    )


def parse_container_opf_path(archive: zipfile.ZipFile) -> str:
    root = parse_epub_xml(read_archive_entry(archive, "META-INF/container.xml"))
    nodes = root.xpath(
        "./ocf:rootfiles/ocf:rootfile[@full-path]",
        namespaces={"ocf": OCF_NAMESPACE},
    )
    if not nodes:
        raise ValueError("META-INF/container.xml contains no OPF rootfile")

    opf_path = nodes[0].get("full-path")
    if not opf_path:
        raise ValueError("Invalid OPF full-path")
    return normalize_epub_path(opf_path)


def parse_manifest(root: etree._Element, opf_dir: str) -> dict[str, dict[str, str]]:
    manifest: dict[str, dict[str, str]] = {}
    for item in root.xpath(".//*[local-name()='manifest']/*[local-name()='item'][@id][@href]"):
        item_id = item.get("id")
        href = item.get("href")
        if not item_id or not href:
            continue
        manifest[item_id] = {
            "path": resolve_href(opf_dir, href),
            "media_type": item.get("media-type") or "",
            "properties": item.get("properties") or "",
        }
    return manifest


def parse_spine_paths(root: etree._Element, manifest: dict[str, dict[str, str]]) -> list[str]:
    paths: list[str] = []
    for itemref in root.xpath(".//*[local-name()='spine']/*[local-name()='itemref'][@idref]"):
        idref = itemref.get("idref")
        if idref and idref in manifest:
            paths.append(manifest[idref]["path"])
    return paths


def find_nav_path(manifest: dict[str, dict[str, str]]) -> str | None:
    for item in manifest.values():
        if "nav" in {part.strip() for part in item["properties"].split()}:
            return item["path"]
    return None


def find_ncx_path(root: etree._Element, manifest: dict[str, dict[str, str]]) -> str | None:
    spine = root.xpath(".//*[local-name()='spine']")
    if spine:
        toc_id = spine[0].get("toc")
        if toc_id and toc_id in manifest:
            return manifest[toc_id]["path"]

    for item in manifest.values():
        if item["media_type"].lower() == "application/x-dtbncx+xml":
            return item["path"]
    return None


def extract_segments(archive: zipfile.ZipFile, package: EpubPackageInfo) -> list[EpubTextSegment]:
    # OPF metadata <dc:title> is intentionally NOT extracted: many users
    # keep the original-language title alongside the translation as a
    # reference, and the title is part of the user's own metadata. NCX
    # nav labels (inner TOC) are still extracted below — those are
    # chapter titles, not the book title.
    segments: list[EpubTextSegment] = []

    processed_paths: set[str] = set()
    for spine_index, doc_path in enumerate(package.spine_paths):
        if not is_html_document_path(doc_path):
            continue
        kind = EpubTextKind.NAV if doc_path == package.nav_path else EpubTextKind.BODY
        append_xhtml_segments(archive, doc_path, spine_index, kind, segments)
        processed_paths.add(doc_path)

    if package.nav_path and package.nav_path not in processed_paths:
        append_xhtml_segments(
            archive,
            package.nav_path,
            ROW_BASE_NAV // ROW_MULTIPLIER,
            EpubTextKind.NAV,
            segments,
        )

    if package.ncx_path:
        append_ncx_segments(archive, package.ncx_path, segments)

    return segments


def is_html_document_path(path: str) -> bool:
    return path.lower().endswith(HTML_DOCUMENT_SUFFIXES)


def append_xhtml_segments(
    archive: zipfile.ZipFile,
    doc_path: str,
    spine_index: int,
    kind: EpubTextKind,
    segments: list[EpubTextSegment],
) -> None:
    root = parse_xhtml_or_html(read_archive_entry(archive, doc_path))
    path_map = build_elem_path_map(root)
    in_skipped_map = build_skipped_map(root)
    has_block_descendant_map = build_has_block_descendant_map(root)
    units = collect_document_units(root, root, path_map, in_skipped_map, has_block_descendant_map)

    unit_index = 0
    for block_path, slots in units:
        part_texts: list[str] = []
        parts: list[EpubPartRef] = []
        has_text = False
        for part, raw_text in slots:
            normalized = normalize_slot_text(raw_text)
            part_texts.append(normalized)
            parts.append(part)
            if raw_text.strip():
                has_text = True
        if not has_text:
            continue
        append_segment(
            segments,
            doc_path=doc_path,
            block_path=block_path,
            part_texts=part_texts,
            parts=parts,
            kind=kind,
            row=spine_index * ROW_MULTIPLIER + unit_index,
        )
        unit_index += 1


def append_ncx_segments(
    archive: zipfile.ZipFile,
    doc_path: str,
    segments: list[EpubTextSegment],
) -> None:
    root = parse_ncx_xml(read_archive_entry(archive, doc_path))
    unit_index = 0
    for elem in root.xpath(".//*[local-name()='text']"):
        text = normalize_slot_text(elem.text or "")
        if not text.strip():
            continue
        elem_path = build_elem_path(root, elem)
        append_segment(
            segments,
            doc_path=doc_path,
            block_path=elem_path,
            part_texts=[text],
            parts=[EpubPartRef(slot="text", path=elem_path)],
            kind=EpubTextKind.NCX,
            row=ROW_BASE_NCX + unit_index,
        )
        unit_index += 1


def append_segment(
    segments: list[EpubTextSegment],
    *,
    doc_path: str,
    block_path: str,
    part_texts: list[str],
    parts: list[EpubPartRef],
    kind: EpubTextKind,
    row: int,
) -> None:
    segments.append(
        EpubTextSegment(
            index=len(segments),
            doc_path=doc_path,
            block_path=block_path,
            text=join_segment_text(part_texts, parts),
            source_digest=sha1_with_null_separator(part_texts),
            kind=kind,
            parts=parts,
            row=row,
        )
    )


def join_segment_text(part_texts: list[str], parts: list[EpubPartRef]) -> str:
    if any("/ruby[" in part.path for part in parts):
        return "".join(part_texts)
    return "\n".join(part_texts)


def collect_document_units(
    root: etree._Element,
    elem: etree._Element,
    path_map: dict[etree._Element, str],
    in_skipped_map: dict[etree._Element, bool],
    has_block_descendant_map: dict[etree._Element, bool],
) -> list[tuple[str, list[tuple[EpubPartRef, str]]]]:
    if in_skipped_map.get(elem, False):
        return []

    units: list[tuple[str, list[tuple[EpubPartRef, str]]]] = []
    is_block = local_name(elem.tag) in BLOCK_TAGS
    has_block_descendant = has_block_descendant_map.get(elem, False)
    elem_path = path_map[elem]

    if is_block and not has_block_descendant:
        slots = iter_translatable_text_slots(root, elem, path_map)
        if slots:
            units.append((elem_path, slots))
        return units

    collect_direct_slots = is_block and has_block_descendant
    if collect_direct_slots and elem.text:
        units.append(
            (elem_path, [(EpubPartRef(slot="text", path=elem_path), elem.text)])
        )

    for child in iter_children_elements(elem):
        if (
            collect_direct_slots
            and local_name(child.tag) not in BLOCK_TAGS
            and not has_block_descendant_map.get(child, False)
        ):
            slots = iter_inline_text_slots(child, path_map)
            if slots:
                units.append((elem_path, slots))
        else:
            units.extend(
                collect_document_units(
                    root, child, path_map, in_skipped_map, has_block_descendant_map
                )
            )
        if collect_direct_slots and child.tail:
            units.append(
                (
                    elem_path,
                    [(EpubPartRef(slot="tail", path=path_map[child]), child.tail)],
                )
            )

    return units


def iter_translatable_text_slots(
    root: etree._Element,
    block: etree._Element,
    path_map: dict[etree._Element, str],
) -> list[tuple[EpubPartRef, str]]:
    results: list[tuple[EpubPartRef, str]] = []

    def walk(elem: etree._Element) -> None:
        if local_name(elem.tag) in SKIP_SUBTREE_TAGS:
            return
        if elem.text:
            results.append((EpubPartRef(slot="text", path=path_map[elem]), elem.text))
        for child in iter_children_elements(elem):
            walk(child)
            if child.tail:
                results.append((EpubPartRef(slot="tail", path=path_map[child]), child.tail))

    walk(block)
    return results


def iter_inline_text_slots(
    elem: etree._Element,
    path_map: dict[etree._Element, str],
) -> list[tuple[EpubPartRef, str]]:
    results: list[tuple[EpubPartRef, str]] = []

    def walk(node: etree._Element) -> None:
        if local_name(node.tag) in SKIP_SUBTREE_TAGS or local_name(node.tag) in BLOCK_TAGS:
            return
        if node.text:
            results.append((EpubPartRef(slot="text", path=path_map[node]), node.text))
        for child in iter_children_elements(node):
            walk(child)
            if child.tail:
                results.append((EpubPartRef(slot="tail", path=path_map[child]), child.tail))

    walk(elem)
    return results


def build_skipped_map(root: etree._Element) -> dict[etree._Element, bool]:
    skipped: dict[etree._Element, bool] = {}
    for elem in iter_elements(root):
        parent = elem.getparent()
        parent_skipped = bool(parent is not None and skipped.get(parent, False))
        skipped[elem] = parent_skipped or local_name(elem.tag) in SKIP_SUBTREE_TAGS
    return skipped


def build_has_block_descendant_map(root: etree._Element) -> dict[etree._Element, bool]:
    elems = list(iter_elements(root))
    has_block_in_subtree: dict[etree._Element, bool] = {}
    has_block_descendant: dict[etree._Element, bool] = {}
    for elem in reversed(elems):
        child_has_block = any(has_block_in_subtree[child] for child in iter_children_elements(elem))
        has_block_descendant[elem] = child_has_block
        has_block_in_subtree[elem] = local_name(elem.tag) in BLOCK_TAGS or child_has_block
    return has_block_descendant


def parse_xhtml_or_html(raw: bytes) -> etree._Element:
    raw = trim_to_html_document_start(raw)
    if not raw.strip():
        return _empty_xhtml_root()
    repaired = repair_redundant_void_end_tags(raw)
    candidates = (raw,) if repaired == raw else (raw, repaired)
    for candidate in candidates:
        try:
            root = etree.fromstring(
                candidate,
                parser=etree.XMLParser(
                    recover=False,
                    resolve_entities=True,
                    no_network=True,
                ),
            )
            if parsed_root_preserves_body(candidate, root):
                return root
        except Exception:
            pass

    for candidate in candidates:
        fixed = normalize_html_named_entities_for_xml(candidate)
        try:
            root = etree.fromstring(
                fixed,
                parser=etree.XMLParser(
                    recover=True,
                    resolve_entities=True,
                    no_network=True,
                ),
            )
            if parsed_root_preserves_body(candidate, root):
                return root
        except Exception:
            pass

    try:
        root = etree.fromstring(raw, parser=etree.HTMLParser(recover=True))
        if root is not None:
            return root
    except Exception as exc:
        raise ValueError("Failed to parse html/xhtml") from exc
    raise ValueError("Failed to parse html/xhtml")


def _empty_xhtml_root() -> etree._Element:
    return etree.Element("html", nsmap={None: "http://www.w3.org/1999/xhtml"})


def repair_redundant_void_end_tags(raw: bytes) -> bytes:
    fixed = RE_REDUNDANT_VOID_END_TAG.sub(lambda match: match.group(1), raw)
    return RE_EMPTY_VOID_PAIR.sub(
        lambda match: b"<" + match.group("tag") + match.group("attrs") + b"/>",
        fixed,
    )


def parsed_root_preserves_body(raw: bytes, root: etree._Element) -> bool:
    if b"<body" not in raw.lower():
        return True
    if local_name(root.tag) == "body":
        return True
    return bool(root.xpath(".//*[local-name()='body']"))


def trim_to_html_document_start(raw: bytes) -> bytes:
    lowered = raw.lower()
    marker_positions = [
        pos
        for marker in (b"<?xml", b"<!doctype", b"<html")
        for pos in [lowered.find(marker)]
        if pos >= 0
    ]
    if not marker_positions:
        return raw
    start = min(marker_positions)
    prefix = raw[:start].lstrip(b"\xef\xbb\xbf").strip()
    if not prefix:
        return raw
    return raw[start:]


def parse_ncx_xml(raw: bytes) -> etree._Element:
    try:
        return etree.fromstring(raw, parser=etree.XMLParser(recover=False, resolve_entities=True, no_network=True))
    except Exception:
        fixed = fix_ncx_bare_ampersands(raw)
        return etree.fromstring(fixed, parser=etree.XMLParser(recover=True, resolve_entities=True, no_network=True))


def parse_epub_xml(raw: bytes) -> etree._Element:
    try:
        return etree.fromstring(
            raw,
            parser=etree.XMLParser(recover=False, resolve_entities=True, no_network=True),
        )
    except etree.XMLSyntaxError:
        fixed = normalize_epub_xml_entities(raw)
        return etree.fromstring(
            fixed,
            parser=etree.XMLParser(recover=True, resolve_entities=True, no_network=True),
        )


def normalize_epub_xml_entities(raw: bytes) -> bytes:
    fixed = normalize_html_named_entities_for_xml(raw)
    return replace_outside_cdata(fixed, RE_NCX_BARE_AMP, lambda match: b"&amp;")


def normalize_html_named_entities_for_xml(raw: bytes) -> bytes:
    if b"&" not in raw:
        return raw

    html5_entities = html.entities.html5

    def replace(match: re.Match[bytes]) -> bytes:
        name_bytes = match.group(1)
        name = name_bytes.decode("ascii")
        value = html5_entities.get(f"{name};") or html5_entities.get(name)
        if value is None:
            return b"&amp;" + name_bytes + b";"
        return "".join(f"&#{ord(ch)};" for ch in value).encode("ascii")

    return replace_outside_cdata(raw, RE_HTML_NAMED_ENTITY, replace)


def fix_ncx_bare_ampersands(raw: bytes) -> bytes:
    return replace_outside_cdata(raw, RE_NCX_BARE_AMP, lambda match: b"&amp;")


def replace_outside_cdata(
    raw: bytes,
    pattern: re.Pattern[bytes],
    replacement,
) -> bytes:
    parts: list[bytes] = []
    last_end = 0
    for match in RE_CDATA_SECTION.finditer(raw):
        parts.append(pattern.sub(replacement, raw[last_end : match.start()]))
        parts.append(raw[match.start() : match.end()])
        last_end = match.end()
    parts.append(pattern.sub(replacement, raw[last_end:]))
    return b"".join(parts)


def build_elem_path(root: etree._Element, elem: etree._Element) -> str:
    segments: list[str] = []
    current: etree._Element | None = elem
    while current is not None and current is not root:
        parent = current.getparent()
        if parent is None:
            break
        name = local_name(current.tag)
        same_name = [child for child in iter_children_elements(parent) if local_name(child.tag) == name]
        position = same_name.index(current) + 1
        segments.append(f"{name}[{position}]")
        current = parent
    segments.append(local_name(root.tag))
    return "/" + "/".join(reversed(segments))


def iter_elem_path_pairs(root: etree._Element) -> Iterator[tuple[etree._Element, str]]:
    root_path = f"/{local_name(root.tag)}"
    stack: list[tuple[etree._Element, str]] = [(root, root_path)]
    while stack:
        parent, parent_path = stack.pop()
        yield parent, parent_path

        counters: dict[str, int] = {}
        child_entries: list[tuple[etree._Element, str]] = []
        for child in iter_children_elements(parent):
            name = local_name(child.tag)
            position = counters.get(name, 0) + 1
            counters[name] = position
            child_entries.append((child, f"{parent_path}/{name}[{position}]"))

        stack.extend(reversed(child_entries))


def build_elem_path_map(root: etree._Element) -> dict[etree._Element, str]:
    return {elem: path for elem, path in iter_elem_path_pairs(root)}


def build_elem_by_path(root: etree._Element) -> dict[str, etree._Element]:
    return {path: elem for elem, path in build_elem_path_map(root).items()}


def find_by_path(root: etree._Element, path: str) -> etree._Element | None:
    if not path.startswith("/"):
        return None
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts or _path_name(parts[0]) != local_name(root.tag):
        return None
    current = root
    for part in parts[1:]:
        name = _path_name(part)
        position = _path_position(part)
        matches = [child for child in iter_children_elements(current) if local_name(child.tag) == name]
        if position < 1 or position > len(matches):
            return None
        current = matches[position - 1]
    return current


def _path_name(part: str) -> str:
    return part.split("[", 1)[0]


def _path_position(part: str) -> int:
    if "[" not in part or not part.endswith("]"):
        return 1
    try:
        return int(part.rsplit("[", 1)[1][:-1])
    except ValueError:
        return 1


def read_archive_entry(archive: zipfile.ZipFile, path: str) -> bytes:
    candidates = []
    for candidate in (path, normalize_epub_path(path)):
        if candidate not in candidates:
            candidates.append(candidate)
    try:
        return archive.read(candidates[0])
    except KeyError:
        pass
    for candidate in candidates[1:]:
        try:
            return archive.read(candidate)
        except KeyError:
            pass
    fallback = find_archive_entry_by_normalized_path(archive, path)
    if fallback:
        return archive.read(fallback)
    raise KeyError(normalize_epub_path(path))


def find_archive_entry_by_normalized_path(archive: zipfile.ZipFile, path: str) -> str:
    normalized = normalize_epub_path(path)
    matches = [
        name
        for name in archive.namelist()
        if normalize_epub_path(name) == normalized
    ]
    return matches[0] if len(matches) == 1 else ""


def iter_elements(root: etree._Element) -> Iterator[etree._Element]:
    for elem in root.iter():
        if isinstance(elem.tag, str):
            yield elem


def iter_children_elements(elem: etree._Element) -> Iterator[etree._Element]:
    for child in elem:
        if isinstance(child.tag, str):
            yield child


def local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def normalize_epub_path(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/"))


def resolve_href(base_dir: str, href: str) -> str:
    joined = posixpath.normpath(posixpath.join(base_dir, normalize_epub_path(href)))
    return joined.lstrip("./")


def normalize_slot_text(text: str) -> str:
    if "\r" not in text and "\n" not in text and "\t" not in text and "  " not in text:
        return text.strip()
    text = RE_SLOT_INLINE_WHITESPACE.sub(" ", text)
    return RE_MULTI_SPACE.sub(" ", text).strip()


def sha1_with_null_separator(parts: list[str]) -> str:
    digest = hashlib.sha1()
    for index, part in enumerate(parts):
        if index:
            digest.update(b"\x00")
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()
