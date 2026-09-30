from __future__ import annotations

import base64
import copy
import hashlib
import html
import json
import mimetypes
import os
import posixpath
import re
import tempfile
import time
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote, urlsplit

from lxml import etree
from lxml import html as lxml_html
import html5lib
import regex
import tinycss2

from transoria.formats.epub_paths import (
    decode_epub_href,
    find_archive_entry_by_normalized_path,
    resolve_epub_href,
)
from transoria.tools.epub_structure import inspect_epub_structure


OPF = "http://www.idpf.org/2007/opf"
XHTML = "http://www.w3.org/1999/xhtml"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
XML_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, remove_blank_text=False
)
TEXT_TYPES = {
    "application/xhtml+xml",
    "text/html",
    "text/plain",
}
XML_TYPES = {
    "application/x-dtbncx+xml",
    "application/xml",
    "text/xml",
    "image/svg+xml",
}
EDITABLE_TYPES = TEXT_TYPES | XML_TYPES | {
    "text/css",
    "application/javascript",
    "text/javascript",
}
MAX_TEXT_BYTES = 4_000_000
MAX_PREVIEW_BYTES = 48_000_000
MAX_PACKAGE_BYTES = 32_000_000
MAX_ARCHIVE_BYTES = 2_000_000_000
MAX_ARCHIVE_ENTRIES = 100_000
SEARCH_TIMEOUT_SECONDS = 3.0
SessionSnapshot = tuple[
    dict[str, bytes], list[str], list[dict[str, object]],
    list[dict[str, object]], set[str], str, str, dict[str, bool],
]


def _xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=XML_PARSER)


def _local_name(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _is_toc(node: etree._Element) -> bool:
    return _local_name(node.tag) == "nav" and (
        "toc" in (node.get("{http://www.idpf.org/2007/ops}type") or node.get("epub:type", "")).split()
        or "doc-toc" in node.get("role", "").split()
    )


def _preview_root(data: bytes, *, html_document: bool = False) -> etree._Element:
    if not html_document:
        try:
            return _xml(data)
        except etree.XMLSyntaxError:
            pass
    text = _decode(data)[0]
    # Remove declared XHTML prefixes only in the disposable HTML5 rendering copy.
    for prefix in re.findall(r'xmlns:([\w.-]+)=[\"\']http://www.w3.org/1999/xhtml[\"\']', text):
        text = re.sub(r'(<\s*/?\s*)' + re.escape(prefix) + ':', r'\1', text)
        text = re.sub(r'\s+xmlns:' + re.escape(prefix) + r'=[\"\']http://www.w3.org/1999/xhtml[\"\']', '', text)
    marker = "data-transoria-source-" + uuid.uuid4().hex
    offsets = [0]
    for line in text.split("\n"):
        offsets.append(offsets[-1] + len(line) + 1)
    insertions: list[tuple[int, str]] = []

    class SourceLines(HTMLParser):
        def handle_starttag(self, tag: str, attrs) -> None:
            line, column = self.getpos()
            opening = re.match(r"<[^\s/>]+", self.get_starttag_text())
            if opening:
                insertions.append((offsets[line - 1] + column + opening.end(), f' {marker}="{line}"'))

        handle_startendtag = handle_starttag

    SourceLines(convert_charrefs=False).feed(text)
    pieces: list[str] = []
    cursor = 0
    for position, attribute in insertions:
        pieces.extend((text[cursor:position], attribute))
        cursor = position
    pieces.append(text[cursor:])
    root = html5lib.parse("".join(pieces), treebuilder="lxml", namespaceHTMLElements=True).getroot()
    for node in root.iter():
        if isinstance(node.tag, str):
            node.set("data-transoria-line", node.attrib.pop(marker, "1"))
    return root


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def _package_bytes(archive: zipfile.ZipFile, path: str) -> bytes:
    if archive.getinfo(path).file_size > MAX_PACKAGE_BYTES:
        raise ValueError(f"EPUB package resource is too large: {path}")
    return archive.read(path)


def _fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _decode(data: bytes) -> tuple[str, str]:
    for signature, encoding in ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                                (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"), (b"\xef\xbb\xbf", "utf-8-sig")):
        if data.startswith(signature):
            return data.decode(encoding), encoding
    head = data[:200].decode("ascii", errors="ignore")
    match = re.search(r"(?:encoding\s*=\s*|@charset\s+)[\"']([^\"']+)", head, re.I)
    encoding = match.group(1) if match else "utf-8"
    try:
        return data.decode(encoding), encoding
    except (LookupError, UnicodeError) as exc:
        raise ValueError(f"Unsupported text encoding: {encoding}") from exc


def _search_pattern(query: str, case_sensitive: bool, regular_expression: bool):
    flags = 0 if case_sensitive else regex.IGNORECASE
    try:
        return regex.compile(query if regular_expression else regex.escape(query), flags)
    except regex.error as exc:
        raise ValueError(f"Invalid regular expression: {exc}") from exc


def _selection_bounds(path: str, content: str, selection: dict[str, object] | None) -> tuple[int, int]:
    if selection is None:
        return 0, len(content)
    start = selection.get("start")
    end = selection.get("end")
    if (
        selection.get("path") != path or not isinstance(start, int)
        or not isinstance(end, int) or not 0 <= start < end <= len(content)
    ):
        raise ValueError("Selected text changed; select it again.")
    return start, end


def _encode(text: str, encoding: str) -> bytes:
    try:
        return text.encode(encoding)
    except (LookupError, UnicodeError) as exc:
        raise ValueError(
            f"Text cannot be encoded as {encoding}; change its declaration to UTF-8."
        ) from exc


STRUCTURAL_TAGS = {
    "html", "head", "body", "nav", "ol", "ul", "li", "div", "section",
    "article", "table", "thead", "tbody", "tfoot", "tr", "dl", "dt", "dd",
    "ncx", "navmap", "navpoint", "navlabel", "pagelist", "pagetarget",
    "doctitle", "docauthor",
}


def _editor_text(data: bytes, media_type: str) -> str:
    original, encoding = _decode(data)
    if media_type not in XML_TYPES | {"application/xhtml+xml"}:
        return original
    try:
        root = _xml(data)
    except etree.XMLSyntaxError:
        return original

    def indent(node: etree._Element, depth: int) -> None:
        children = [child for child in node if isinstance(child.tag, str)]
        if not children:
            return
        structural = (
            etree.QName(node).localname.lower() in STRUCTURAL_TAGS
            and not (node.text or "").strip()
            and all(not (child.tail or "").strip() for child in node)
        )
        if structural:
            node.text = "\n" + "  " * (depth + 1)
            for index, child in enumerate(node):
                child.tail = "\n" + "  " * (depth if index == len(node) - 1 else depth + 1)
        for child in children:
            indent(child, depth + 1 if structural else depth)

    indent(root, 0)
    formatted = etree.tostring(root.getroottree(), encoding="utf-8" if encoding == "utf-8-sig" else encoding, xml_declaration=True)
    return _decode(formatted)[0]


def _document_ids(data: bytes) -> set[str]:
    try:
        root = _xml(data)
    except etree.XMLSyntaxError:
        root = lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True))
    return {
        value
        for node in root.iter()
        if isinstance(node.tag, str)
        for value in (
            node.get("id"),
            node.get("{http://www.w3.org/XML/1998/namespace}id"),
            node.get("name"),
        )
        if value
    }


def _rewrite_resource_links(
    data: bytes, media: str, source_path: str, destination_path: str,
    renamed: dict[str, str], known_paths: set[str],
    fragments: dict[tuple[str, str], str] | None = None,
    fragment_paths: dict[tuple[str, str], str] | None = None,
) -> tuple[bytes, set[str]]:
    referenced: set[str] = set()
    changed = False

    def rewrite_url(raw: str, section_target: bool = True) -> str:
        nonlocal changed
        if not raw or raw.startswith(("data:", "//")) or urlsplit(raw).scheme:
            return raw
        parts = urlsplit(raw)
        target = posixpath.normpath(posixpath.join(
            posixpath.dirname(source_path), unquote(parts.path),
        )) if parts.path else source_path
        if target not in known_paths:
            if source_path != destination_path:
                raise ValueError(f"Cannot move a resource with an unresolved link: {raw}")
            return raw
        referenced.add(target)
        new_target = (fragment_paths or {}).get((target, unquote(parts.fragment)), renamed.get(target, target)) if section_target else renamed.get(target, target)
        fragment = (fragments or {}).get((target, unquote(parts.fragment)), parts.fragment) if section_target else parts.fragment
        if source_path == destination_path and new_target == target and fragment == parts.fragment:
            return raw
        relative = posixpath.relpath(new_target, posixpath.dirname(destination_path) or ".")
        updated = quote(relative, safe="/-._~")
        if parts.query:
            updated += f"?{parts.query}"
        if fragment:
            updated += f"#{fragment}"
        changed |= updated != raw
        return updated

    def rewrite_css(css: str, *, declarations: bool = False) -> str:
        css_changed = False

        def css_url(raw: str) -> str:
            nonlocal css_changed
            updated = rewrite_url(raw)
            css_changed |= updated != raw
            return updated
        rules = (
            tinycss2.parse_declaration_list(css, skip_comments=False, skip_whitespace=False)
            if declarations else tinycss2.parse_stylesheet(css, skip_comments=False, skip_whitespace=False)
        )

        def walk(tokens: list[object]) -> None:
            for token in tokens:
                token_type = getattr(token, "type", "")
                if token_type == "error":
                    raise ValueError("Cannot safely rewrite malformed CSS references.")
                if token_type == "url":
                    updated = css_url(token.value)
                    if updated != token.value:
                        token.value = updated
                        token.representation = f"url({json.dumps(updated, ensure_ascii=False)})"
                elif token_type == "function" and token.lower_name == "url":
                    values = [part for part in token.arguments if part.type != "whitespace"]
                    if len(values) == 1 and values[0].type == "string":
                        value = values[0]
                        updated = css_url(value.value)
                        if updated != value.value:
                            value.value = updated
                            value.representation = json.dumps(updated, ensure_ascii=False)
                    else:
                        raise ValueError("Cannot safely rewrite a complex CSS url().")
                elif hasattr(token, "content") and token.content is not None:
                    walk(token.content)
                elif hasattr(token, "arguments"):
                    walk(token.arguments)

        for rule in rules:
            if rule.type == "error":
                raise ValueError("Cannot safely rewrite malformed CSS references.")
            if rule.type == "at-rule" and rule.lower_at_keyword == "import":
                first = next((token for token in rule.prelude if token.type not in {"whitespace", "comment"}), None)
                if first is not None and first.type == "string":
                    updated = css_url(first.value)
                    if updated != first.value:
                        first.value = updated
                        first.representation = json.dumps(updated, ensure_ascii=False)
            if hasattr(rule, "prelude"):
                walk(rule.prelude)
            if getattr(rule, "value", None) is not None and isinstance(rule.value, list):
                walk(rule.value)
            if getattr(rule, "content", None) is not None:
                walk(rule.content)
        return tinycss2.serialize(rules) if css_changed else css

    if media == "text/css":
        content, encoding = _decode(data)
        updated = rewrite_css(content)
        return (_encode(updated, encoding) if changed else data), referenced

    if media not in {
        "application/xhtml+xml", "text/html", "application/x-dtbncx+xml",
        "application/xml", "text/xml", "image/svg+xml",
    }:
        if media.startswith("text/") or media.endswith("+xml") or "javascript" in media:
            if source_path != destination_path or any(
                posixpath.basename(path).encode("utf-8") in data
                or quote(posixpath.basename(path)).encode("ascii") in data
                for path in renamed
            ):
                raise ValueError(f"Cannot safely rewrite unsupported text resource: {source_path}")
        return data, referenced
    try:
        root = (
            lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True))
            if media == "text/html" else _xml(data)
        )
    except (etree.XMLSyntaxError, etree.ParserError) as exc:
        if source_path != destination_path or any(
            posixpath.basename(path).encode("utf-8") in data
            or quote(posixpath.basename(path)).encode("ascii") in data
            for path in renamed
        ):
            raise ValueError(f"Cannot safely rewrite malformed document: {source_path}") from exc
        return data, referenced
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        for key, value in list(node.attrib.items()):
            name = etree.QName(key).localname.lower()
            if name in {"href", "src", "poster", "data"}:
                updated = rewrite_url(value, section_target=node.tag != f"{{{OPF}}}item")
                if updated != value:
                    node.set(key, updated)
            elif name == "srcset":
                items = []
                for part in value.split(","):
                    match = re.match(r"(\s*)(\S+)(.*)", part, flags=re.S)
                    if not match:
                        items.append(part)
                        continue
                    items.append(match.group(1) + rewrite_url(match.group(2)) + match.group(3))
                updated = ",".join(items)
                if updated != value:
                    node.set(key, updated)
            elif name == "style":
                css = rewrite_css(value, declarations=True)
                if css != value:
                    node.set(key, css)
        if etree.QName(node).localname.lower() == "style" and node.text:
            css = rewrite_css(node.text)
            if css != node.text:
                node.text = css
    if not changed:
        return data, referenced
    return etree.tostring(
        root.getroottree() if media != "text/html" else root,
        encoding=_decode(data)[1], xml_declaration=media != "text/html",
        method="html" if media == "text/html" else "xml",
    ), referenced


def _missing_toc_fragments(
    archive: zipfile.ZipFile, entries: list[dict[str, object]]
) -> set[str]:
    ids_by_path: dict[str, set[str]] = {}
    missing: set[str] = set()
    for entry in entries:
        href = str(entry["href"])
        path, separator, raw_fragment = href.partition("#")
        fragment = unquote(raw_fragment)
        if not separator or not fragment or fragment.startswith("epubcfi("):
            continue
        if path not in ids_by_path:
            try:
                ids_by_path[path] = (
                    _document_ids(archive.read(path))
                    if archive.getinfo(path).file_size <= MAX_PREVIEW_BYTES
                    else set()
                )
            except (KeyError, etree.ParserError, etree.XMLSyntaxError):
                ids_by_path[path] = set()
        if fragment not in ids_by_path[path]:
            missing.add(href)
    return missing


@dataclass
class ContentSession:
    path: Path
    fingerprint: tuple[int, int]
    opf_path: str
    files: list[dict[str, object]]
    spine: list[str]
    toc: list[dict[str, object]]
    nav_path: str
    ncx_path: str
    spine_linear: dict[str, bool] = field(default_factory=dict)
    changes: dict[str, bytes] = field(default_factory=dict)
    removed: set[str] = field(default_factory=set)
    dirty: bool = False
    undo_stack: list[SessionSnapshot] = field(default_factory=list)
    redo_stack: list[SessionSnapshot] = field(default_factory=list)
    checkpoints: dict[str, SessionSnapshot] = field(default_factory=dict)
    clean_snapshot: SessionSnapshot = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.clean_snapshot = self._snapshot()

    @classmethod
    def open(cls, path: str) -> ContentSession:
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != ".epub":
            raise ValueError("Select an existing EPUB file.")
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_ARCHIVE_ENTRIES or sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise ValueError("EPUB exceeds the editor archive limit.")
            if len(names) != len(set(names)):
                raise ValueError("EPUB has duplicate archive paths; repair it before editing.")
            if any(
                name.startswith("/") or "\\" in name or ".." in name.split("/")
                for name in names
            ):
                raise ValueError("EPUB has unsafe archive paths.")
            container = _xml(_package_bytes(archive, "META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise ValueError("EPUB package document is missing.")
            opf_path = find_archive_entry_by_normalized_path(
                archive, rootfile.get("full-path")
            )
            if not opf_path:
                raise ValueError("EPUB package document is missing.")
            opf = _xml(_package_bytes(archive, opf_path))
            files: list[dict[str, object]] = []
            id_to_path: dict[str, str] = {}
            nav_path = ncx_path = ""
            for item in opf.findall(f".//{{{OPF}}}manifest/{{{OPF}}}item"):
                item_id = item.get("id", "")
                media = item.get("media-type", "")
                resolved = resolve_epub_href(
                    posixpath.dirname(opf_path), item.get("href", "")
                )
                entry = find_archive_entry_by_normalized_path(archive, resolved)
                if not entry:
                    continue
                size = archive.getinfo(entry).file_size
                if media in EDITABLE_TYPES and size > MAX_PREVIEW_BYTES:
                    raise ValueError(f"EPUB text resource is too large to edit safely: {entry}")
                id_to_path[item_id] = entry
                files.append(
                    {
                        "path": entry,
                        "media_type": media,
                        "editable": media in EDITABLE_TYPES,
                        "size": size,
                    }
                )
                if "nav" in item.get("properties", "").split():
                    nav_path = entry
                if media == "application/x-dtbncx+xml":
                    ncx_path = entry
            spine_items = [
                (id_to_path[item.get("idref", "")], item.get("linear") != "no")
                for item in opf.findall(f".//{{{OPF}}}spine/{{{OPF}}}itemref")
                if item.get("idref", "") in id_to_path
            ]
            spine = [path for path, _ in spine_items]
            toc = _read_toc(archive, nav_path, ncx_path, tolerant=True)
        return cls(
            source,
            _fingerprint(source),
            opf_path,
            files,
            spine,
            toc,
            nav_path,
            ncx_path,
            dict(spine_items),
        )

    def info(self, session_id: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "input_path": str(self.path),
            "files": self.files,
            "spine": self.spine,
            "spine_linear": self.spine_linear,
            "toc": self.toc,
            "nav_path": self.nav_path,
            "ncx_path": self.ncx_path,
            "dirty": self.dirty,
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "checkpoints": list(self.checkpoints),
        }

    def _snapshot(self) -> SessionSnapshot:
        return (
            self.changes.copy(), self.spine.copy(), copy.deepcopy(self.toc),
            copy.deepcopy(self.files), self.removed.copy(), self.nav_path,
            self.ncx_path, self.spine_linear.copy(),
        )

    def _record(self) -> None:
        self.undo_stack.append(self._snapshot())
        self.undo_stack = self.undo_stack[-30:]
        self.redo_stack.clear()
        self.dirty = True

    @contextmanager
    def transaction(self):
        before = self._snapshot()
        undo, redo, dirty = self.undo_stack.copy(), self.redo_stack.copy(), self.dirty
        try:
            yield
        except Exception:
            (
                self.changes, self.spine, self.toc, self.files, self.removed,
                self.nav_path, self.ncx_path, self.spine_linear,
            ) = before
            self.undo_stack, self.redo_stack, self.dirty = undo, redo, dirty
            raise
        if self._snapshot() != before:
            self.undo_stack = (undo + [before])[-30:]
            self.redo_stack = []
            self.dirty = True

    def write_many(self, buffers: dict[str, str]) -> None:
        with self.transaction():
            for path, content in buffers.items():
                self.write(path, content)

    def named_checkpoint(self, name: str) -> None:
        name = name.strip()
        if not name or len(name) > 80 or name in self.checkpoints:
            raise ValueError("Use a unique checkpoint name under 80 characters.")
        if len(self.checkpoints) >= 10:
            raise ValueError("Keep at most ten checkpoints per editing session.")
        self.checkpoints[name] = self._snapshot()

    def restore_checkpoint(self, name: str) -> None:
        if name not in self.checkpoints:
            raise ValueError("Checkpoint does not exist.")
        self._record()
        (
            self.changes, self.spine, self.toc, self.files, self.removed,
            self.nav_path, self.ncx_path, self.spine_linear,
        ) = copy.deepcopy(self.checkpoints[name])
        self.dirty = self._snapshot() != self.clean_snapshot

    def history(self, direction: str) -> None:
        source = self.undo_stack if direction == "undo" else self.redo_stack
        target = self.redo_stack if direction == "undo" else self.undo_stack
        if not source:
            raise ValueError("Nothing to undo or redo.")
        target.append(self._snapshot())
        (
            self.changes, self.spine, self.toc, self.files, self.removed,
            self.nav_path, self.ncx_path, self.spine_linear,
        ) = source.pop()
        self.dirty = self._snapshot() != self.clean_snapshot

    def _file(self, path: str, *, editable: bool = False) -> dict[str, object]:
        found = next((item for item in self.files if item["path"] == path), None)
        if found is None or (editable and not found["editable"]):
            raise ValueError("Resource is not editable or is not in the EPUB manifest.")
        return found

    def _bytes(self, path: str) -> bytes:
        if path in self.changes:
            return self.changes[path]
        if path in self.removed:
            raise ValueError("Resource was removed from the editor session.")
        with zipfile.ZipFile(self.path) as archive:
            return archive.read(path)

    def read(self, path: str) -> dict[str, object]:
        item = self._file(path, editable=True)
        if int(item["size"]) > MAX_TEXT_BYTES and path not in self.changes:
            raise ValueError(
                "This resource is too large for the source editor (4 MB limit)."
            )
        data = self._bytes(path)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError(
                "This resource is too large for the source editor (4 MB limit)."
            )
        content = _editor_text(data, str(item["media_type"]))
        encoding = _decode(data)[1]
        return {"path": path, "content": content, "encoding": encoding}

    def anchors(self, path: str) -> list[dict[str, str]]:
        item = self._file(path)
        if item["media_type"] not in {"application/xhtml+xml", "text/html"}:
            raise ValueError("TOC targets must be XHTML or HTML resources.")
        data = self._bytes(path)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError("Chapter exceeds the 4 MB editor limit.")
        try:
            root = _xml(data)
        except etree.XMLSyntaxError:
            root = lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True))
        anchors: list[dict[str, str]] = []
        seen: set[str] = set()
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            identifier = node.get("id") or node.get("{http://www.w3.org/XML/1998/namespace}id")
            if not identifier and etree.QName(node).localname.lower() == "a":
                identifier = node.get("name")
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            label = " ".join("".join(node.itertext()).split())[:80]
            anchors.append({"id": identifier, "label": label or identifier})
            if len(anchors) >= 5000:
                break
        return anchors

    def write(self, path: str, content: str) -> None:
        self._file(path, editable=True)
        if len(content.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("Resource exceeds the 4 MB editor limit.")
        encoding = _decode(self._bytes(path))[1]
        updated = _encode(content, encoding)
        if updated == self._bytes(path):
            return
        self._apply_changes({path: updated})

    def _toc_updates(self, entries: list[dict[str, object]]) -> dict[str, bytes]:
        pending = {}
        for path, writer in ((self.nav_path, _write_nav), (self.ncx_path, _write_ncx)):
            if path:
                data = writer(self._bytes(path), path, entries)
                if len(data) > MAX_TEXT_BYTES:
                    raise ValueError("Navigation exceeds the 4 MB editor limit.")
                pending[path] = data
        return pending

    def _apply_changes(self, pending: dict[str, bytes]) -> None:
        pending = pending.copy()
        toc = None
        edited = self.nav_path if self.nav_path in pending else self.ncx_path if self.ncx_path in pending else ""
        if edited:
            try:
                with zipfile.ZipFile(self.path) as archive:
                    toc = _read_toc(archive, edited if edited == self.nav_path else "", edited if edited == self.ncx_path else "", pending)
            except etree.XMLSyntaxError:
                # Incomplete navigation source remains editable as a draft.
                pass
            if toc is not None:
                companion = self.ncx_path if edited == self.nav_path else self.nav_path
                if companion and companion not in pending:
                    writer = _write_ncx if companion == self.ncx_path else _write_nav
                    try:
                        data = writer(self._bytes(companion), companion, toc)
                    except etree.XMLSyntaxError:
                        pass
                    else:
                        if len(data) > MAX_TEXT_BYTES:
                            raise ValueError("Navigation exceeds the 4 MB editor limit.")
                        pending[companion] = data
        self._record()
        self.changes.update(pending)
        if toc is not None:
            self.toc = toc
        for item in self.files:
            if item["path"] in pending:
                item["size"] = len(pending[str(item["path"])])

    def _resource_paths(self) -> set[str]:
        with zipfile.ZipFile(self.path) as archive:
            return (set(archive.namelist()) - self.removed) | set(self.changes)

    def _check_new_path(self, path: str) -> None:
        if (
            not path or path.startswith("/") or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(ord(char) < 32 for char in path)
            or path.startswith("META-INF/") or path == "mimetype"
            or path == self.opf_path
        ):
            raise ValueError("Unsafe EPUB resource path.")
        names = self._resource_paths()
        key = unicodedata.normalize("NFC", unquote(path)).casefold()
        if any(unicodedata.normalize("NFC", unquote(name)).casefold() == key for name in names):
            raise ValueError("EPUB resource path already exists.")

    def _package(self) -> etree._Element:
        return _xml(self._bytes(self.opf_path))

    def add_resource(
        self, path: str, data: bytes, media_type: str = "", in_spine: bool = False,
    ) -> None:
        self._check_new_path(path)
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError("Resource exceeds the 48 MB editor limit.")
        media = media_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if in_spine and media not in {"application/xhtml+xml", "text/html"}:
            raise ValueError("Only XHTML/HTML resources can enter the reading order.")
        if media in XML_TYPES | {"application/xhtml+xml"}:
            try:
                _xml(data)
            except etree.XMLSyntaxError as exc:
                raise ValueError(f"Invalid XML in {path}: {exc}") from exc
        package = self._package()
        manifest = package.find(f"{{{OPF}}}manifest")
        spine = package.find(f"{{{OPF}}}spine")
        if manifest is None or spine is None:
            raise ValueError("EPUB manifest or reading order is missing.")
        used = {item.get("id") for item in manifest}
        index = 1
        while f"transoria-resource-{index}" in used:
            index += 1
        item_id = f"transoria-resource-{index}"
        href = quote(posixpath.relpath(path, posixpath.dirname(self.opf_path) or "."), safe="/-._~")
        etree.SubElement(manifest, f"{{{OPF}}}item", id=item_id, href=href, **{"media-type": media})
        if in_spine:
            etree.SubElement(spine, f"{{{OPF}}}itemref", idref=item_id)
        self._record()
        self.changes[self.opf_path] = _serialize(package)
        self.changes[path] = data
        self.files.append({
            "path": path, "media_type": media,
            "editable": media in EDITABLE_TYPES, "size": len(data),
        })
        if in_spine:
            self.spine.append(path)
            self.spine_linear[path] = True

    def create_chapter(self, path: str, title: str, body_text: str, after_path: str = "") -> None:
        title = title.strip()
        if not title or not path.lower().endswith(".xhtml"):
            raise ValueError("A chapter needs a title and an .xhtml path.")
        if after_path and after_path not in self.spine:
            raise ValueError("The chapter insertion point is not in the reading order.")
        root = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML})
        head = etree.SubElement(root, f"{{{XHTML}}}head")
        etree.SubElement(head, f"{{{XHTML}}}title").text = title
        body = etree.SubElement(root, f"{{{XHTML}}}body")
        etree.SubElement(body, f"{{{XHTML}}}h1").text = title
        for paragraph in body_text.splitlines():
            if paragraph.strip():
                etree.SubElement(body, f"{{{XHTML}}}p").text = paragraph
        data = _serialize(root)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError("Resource exceeds the 4 MB editor limit.")
        self.add_resource(path, data, "application/xhtml+xml", in_spine=True)
        if after_path:
            self.spine.remove(path)
            self.spine.insert(self.spine.index(after_path) + 1, path)

    def split_points(self, path: str) -> list[dict[str, object]]:
        item = self._file(path, editable=True)
        if item["media_type"] != "application/xhtml+xml" or path not in self.spine:
            raise ValueError("Only XHTML chapters in the reading order can be split.")
        root = _xml(self._bytes(path))
        body = root.find(f".//{{{XHTML}}}body")
        if body is None or len(body) < 2:
            raise ValueError("This chapter has no top-level body elements to split.")
        if (body.text or "").strip() or any((child.tail or "").strip() for child in body):
            raise ValueError("This chapter has loose body text; split it in the source editor first.")
        return [
            {
                "index": index,
                "label": " ".join("".join(child.itertext()).split())[:90]
                or etree.QName(child).localname,
            }
            for index, child in enumerate(body) if index > 0 and isinstance(child.tag, str)
        ]

    def split_chapter(self, path: str, target: str, index: int) -> None:
        points = self.split_points(path)
        if not isinstance(index, int) or index not in {point["index"] for point in points}:
            raise ValueError("Choose an existing chapter split point.")
        if posixpath.dirname(target) != posixpath.dirname(path) or not target.lower().endswith(".xhtml"):
            raise ValueError("The new chapter must be an .xhtml file in the same folder.")
        self._check_new_path(target)
        root = _xml(self._bytes(path))
        first = copy.deepcopy(root)
        second = copy.deepcopy(root)
        first_body = first.find(f".//{{{XHTML}}}body")
        second_body = second.find(f".//{{{XHTML}}}body")
        assert first_body is not None and second_body is not None
        for child in list(first_body)[index:]:
            first_body.remove(child)
        for child in list(second_body)[:index]:
            second_body.remove(child)
        moved_ids = {
            value for node in second_body.iter() for value in
            (node.get("id"), node.get("{http://www.w3.org/XML/1998/namespace}id"), node.get("name"))
            if value
        }
        retained_ids = {
            value for node in first_body.iter() for value in
            (node.get("id"), node.get("{http://www.w3.org/XML/1998/namespace}id"), node.get("name"))
            if value
        }
        moved_ids -= retained_ids
        destinations = {(path, identifier): target for identifier in moved_ids}
        known = self._resource_paths()
        before, _ = _rewrite_resource_links(_serialize(first), "application/xhtml+xml", path, path, {}, known, fragment_paths=destinations)
        after, _ = _rewrite_resource_links(_serialize(second), "application/xhtml+xml", path, target, {}, known, fragment_paths=destinations)
        pending = {}
        for source, media in [(self.opf_path, "application/xml"), *((str(item["path"]), str(item["media_type"])) for item in self.files if item["path"] != path)]:
            data = self._bytes(source)
            if len(data) > MAX_TEXT_BYTES and media in EDITABLE_TYPES:
                raise ValueError(f"Cannot inspect oversized references: {source}")
            updated, _ = _rewrite_resource_links(data, media, source, source, {}, known, fragment_paths=destinations)
            if updated != data:
                pending[source] = updated
        toc = []
        for entry in self.toc:
            href = str(entry["href"])
            if href.split("#", 1)[0] == path and unquote(href.partition("#")[2]) in moved_ids:
                entry = {**entry, "href": target + "#" + href.partition("#")[2]}
            toc.append(entry)
        if len(before) > MAX_TEXT_BYTES or len(after) > MAX_TEXT_BYTES:
            raise ValueError("Split chapters exceed the 4 MB editor limit.")
        with self.transaction():
            self.changes.update(pending)
            self.add_resource(target, after, "application/xhtml+xml", in_spine=True)
            self.changes[path] = before
            self._file(path)["size"] = len(before)
            self.spine.remove(target)
            self.spine.insert(self.spine.index(path) + 1, target)
            self.toc = toc

    def replace_resource(self, path: str, data: bytes) -> None:
        self._file(path)
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError("Resource exceeds the 48 MB editor limit.")
        if data == self._bytes(path):
            return
        self._apply_changes({path: data})

    def split_style(self, path: str, target: str, index: int) -> None:
        if self._file(path)["media_type"] != "text/css":
            raise ValueError("Choose a CSS stylesheet.")
        self._check_new_path(target)
        text, encoding = _decode(self._bytes(path))
        rules = tinycss2.parse_stylesheet(text, skip_comments=False, skip_whitespace=False)
        if any(rule.type == "error" for rule in rules):
            raise ValueError("Cannot split malformed CSS.")
        if not isinstance(index, int) or not 0 < index < len(rules):
            raise ValueError("Choose a CSS rule boundary.")
        prefix, suffix = tinycss2.serialize(rules[:index]), tinycss2.serialize(rules[index:])
        # Importing the prefix preserves cascade order even when the source has @imports.
        prefix_bytes, _ = _rewrite_resource_links(
            _encode(prefix, encoding), "text/css", path, target, {}, self._resource_paths(),
        )
        href = quote(posixpath.relpath(target, posixpath.dirname(path) or "."), safe="/-._~")
        charset = f'@charset "{encoding}";\n' if encoding.lower().replace("-", "") not in {"utf8", "utf8sig"} else ""
        suffix = charset + f'@import "{href}";\n' + suffix
        with self.transaction():
            self.add_resource(target, prefix_bytes, "text/css")
            self.write(path, suffix)

    def style_split_points(self, path: str) -> list[dict[str, object]]:
        if self._file(path)["media_type"] != "text/css":
            raise ValueError("Choose a CSS stylesheet.")
        rules = tinycss2.parse_stylesheet(_decode(self._bytes(path))[0])
        if any(rule.type == "error" for rule in rules):
            raise ValueError("Cannot split malformed CSS.")
        return [
            {"index": index, "label": tinycss2.serialize([rule]).strip()[:90]}
            for index, rule in enumerate(rules)
            if index > 0 and rule.type in {"qualified-rule", "at-rule"}
        ]

    def merge_resources(self, paths: list[str]) -> str:
        if len(paths) < 2 or len(set(paths)) != len(paths):
            raise ValueError("Choose at least two distinct files in the desired merge order.")
        media = str(self._file(paths[0])["media_type"])
        if media not in {"application/xhtml+xml", "text/css"} or any(
            self._file(path)["media_type"] != media for path in paths
        ):
            raise ValueError("Merge only XHTML chapters or only CSS stylesheets.")
        if any(path in {self.nav_path, self.ncx_path} for path in paths):
            raise ValueError("Navigation files cannot be merged as chapters.")
        target = paths[0]
        if media == "application/xhtml+xml" and any(path in self.spine for path in paths) and target not in self.spine:
            raise ValueError("The first merged chapter must be in the reading order.")
        mapping = {path: target for path in paths}
        fragments: dict[tuple[str, str], str] = {}
        known = self._resource_paths()
        roots = []
        if media == "application/xhtml+xml":
            used_ids: set[str] = set()
            for number, path in enumerate(paths):
                root = _xml(self._bytes(path))
                body = root.find(f".//{{{XHTML}}}body")
                if body is None or root.find(f"{{{XHTML}}}head") is None or root.findall(f".//{{{XHTML}}}script"):
                    raise ValueError("Merge requires XHTML heads and bodies without scripts.")
                ids = _document_ids(self._bytes(path))
                if used_ids & ids:
                    raise ValueError("Chapters contain duplicate IDs; rename them before merging.")
                used_ids |= ids
                anchor = f"merged-section-{number + 1}"
                while anchor in used_ids:
                    anchor += "-"
                used_ids.add(anchor)
                fragments[(path, "")] = anchor
                roots.append((path, root, body, anchor))
            merged = copy.deepcopy(roots[0][1])
            head = merged.find(f"{{{XHTML}}}head")
            body = merged.find(f"{{{XHTML}}}body")
            assert head is not None and body is not None
            body.clear()
            for path, root, source_body, anchor in roots:
                section = etree.SubElement(body, f"{{{XHTML}}}div", id=anchor)
                for key, value in source_body.attrib.items():
                    if key != "id":
                        section.set(key, value)
                if source_body.get("id"):
                    wrapper = etree.SubElement(section, f"{{{XHTML}}}div", id=source_body.get("id"))
                else:
                    wrapper = section
                wrapper.text = source_body.text
                for child in source_body:
                    wrapper.append(copy.deepcopy(child))
                rewritten, _ = _rewrite_resource_links(
                    _serialize(section), media, path, target, mapping, known, fragments,
                )
                body.replace(section, _xml(rewritten))
                if path != target:
                    source_head = root.find(f"{{{XHTML}}}head")
                    for child in source_head if source_head is not None else []:
                        if etree.QName(child).localname not in {"link", "style"}:
                            continue
                        rewritten, _ = _rewrite_resource_links(
                            _serialize(child), media, path, target, mapping, known, fragments,
                        )
                        if not any(_serialize(existing) == rewritten for existing in head):
                            head.append(_xml(rewritten))
            merged_bytes = _serialize(merged)
        else:
            contents = []
            for path in paths:
                data, _ = _rewrite_resource_links(self._bytes(path), media, path, target, {}, known)
                rules = tinycss2.parse_stylesheet(_decode(data)[0], skip_comments=False)
                if any(rule.type == "error" or (rule.type == "at-rule" and rule.lower_at_keyword == "import") for rule in rules):
                    raise ValueError("Inline CSS imports and fix syntax errors before merging stylesheets.")
                contents.append(tinycss2.serialize([rule for rule in rules if not (
                    rule.type == "at-rule" and rule.lower_at_keyword == "charset"
                )]))
            merged_bytes = ("\n".join(contents)).encode("utf-8")
        if len(merged_bytes) > MAX_TEXT_BYTES:
            raise ValueError("Merged file exceeds the 4 MB editor limit.")
        pending: dict[str, bytes] = {target: merged_bytes}
        for item in self.files:
            path = str(item["path"])
            if path in paths:
                continue
            updated, _ = _rewrite_resource_links(
                self._bytes(path), str(item["media_type"]), path, path, mapping, known, fragments,
            )
            if updated != self._bytes(path):
                pending[path] = updated
        package = self._package()
        removed_ids = set()
        manifest = package.find(f"{{{OPF}}}manifest")
        assert manifest is not None
        for item in list(manifest):
            if resolve_epub_href(posixpath.dirname(self.opf_path), item.get("href", "")) in paths[1:]:
                removed_ids.add(item.get("id"))
                manifest.remove(item)
        for item in package.findall(f"{{{OPF}}}spine/{{{OPF}}}itemref"):
            if item.get("idref") in removed_ids:
                item.getparent().remove(item)
        pending[self.opf_path], _ = _rewrite_resource_links(
            _serialize(package), "application/xml", self.opf_path, self.opf_path,
            mapping, known, fragments,
        )
        toc = []
        for entry in self.toc:
            path, _, fragment = str(entry["href"]).partition("#")
            if path in mapping:
                fragment = fragments.get((path, unquote(fragment)), fragment)
                entry = {**entry, "href": target + (f"#{fragment}" if fragment else "")}
            toc.append(entry)
        self._record()
        self.changes.update(pending)
        for path in paths[1:]:
            self.changes.pop(path, None)
            self.removed.add(path)
            self.spine_linear.pop(path, None)
        self.files = [item for item in self.files if item["path"] not in paths[1:]]
        self._file(target)["size"] = len(merged_bytes)
        self.spine = [path for path in self.spine if path not in paths[1:]]
        self.toc = toc
        return target

    def export_resource(self, path: str, output_path: str, overwrite: bool) -> str:
        self._file(path)
        output = Path(output_path).expanduser().resolve()
        if not output.parent.is_dir():
            raise ValueError("Output folder does not exist.")
        if output == self.path:
            raise ValueError("Resource export cannot overwrite the source EPUB.")
        if output.exists() and not overwrite:
            raise ValueError("Output exists; confirm overwrite first.")
        data = self._bytes(path)
        fd, temp_name = tempfile.mkstemp(prefix=".epub-resource-", dir=output.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            if overwrite:
                os.replace(temp, output)
            else:
                try:
                    os.link(temp, output)
                except FileExistsError as exc:
                    raise ValueError("Output exists; confirm overwrite first.") from exc
        finally:
            temp.unlink(missing_ok=True)
        return str(output)

    def resource_references(self, path: str) -> list[str]:
        self._file(path)
        known = self._resource_paths()
        inbound = []
        for item in self.files:
            source_path = str(item["path"])
            if source_path == path:
                continue
            media = str(item["media_type"])
            if media not in EDITABLE_TYPES and not media.endswith("+xml"):
                continue
            _, targets = _rewrite_resource_links(
                self._bytes(source_path), media,
                source_path, source_path, {path: path}, known,
            )
            if path in targets:
                inbound.append(source_path)
        with zipfile.ZipFile(self.path) as archive:
            manifest_paths = {str(item["path"]) for item in self.files}
            names = (set(archive.namelist()) - self.removed) - manifest_paths
            for source_path in names - {self.opf_path, "mimetype"}:
                info = archive.getinfo(source_path)
                if info.file_size > MAX_TEXT_BYTES:
                    continue
                data = self.changes.get(source_path, archive.read(source_path))
                if any(candidate in data for candidate in {
                    path.encode("utf-8"), quote(path).encode("ascii"),
                    posixpath.basename(path).encode("utf-8"),
                }):
                    inbound.append(source_path)
        if path in self.spine:
            inbound.append("OPF spine")
        if any(str(entry["href"]).split("#", 1)[0] == path for entry in self.toc):
            inbound.append("EPUB navigation")
        return inbound

    def rename_resource(self, path: str, target: str) -> dict[str, int]:
        item = self._file(path)
        if path in {self.nav_path, self.ncx_path}:
            raise ValueError("Navigation resources cannot be moved yet.")
        self._check_new_path(target)
        with zipfile.ZipFile(self.path) as archive:
            if "META-INF/encryption.xml" in archive.namelist():
                encrypted = archive.read("META-INF/encryption.xml")
                if path.encode("utf-8") in encrypted or quote(path).encode("ascii") in encrypted:
                    raise ValueError("Encrypted resources cannot be moved safely.")
        known = self._resource_paths()
        renamed = {path: target}
        pending: dict[str, bytes] = {}
        sources = [(self.opf_path, "application/xml")] + [
            (str(file["path"]), str(file["media_type"])) for file in self.files
        ]
        for source_path, media in sources:
            destination = target if source_path == path else source_path
            data, _ = _rewrite_resource_links(
                self._bytes(source_path), media, source_path, destination,
                renamed, known,
            )
            if destination != source_path or data != self._bytes(source_path):
                pending[destination] = data
        updated_toc = [
            {**entry, "href": target + str(entry["href"])[len(path):]}
            if str(entry["href"]).split("#", 1)[0] == path else entry
            for entry in self.toc
        ]
        self._record()
        self.removed.add(path)
        self.changes.pop(path, None)
        self.changes.update(pending)
        item["path"] = target
        self.spine = [target if current == path else current for current in self.spine]
        if path in self.spine_linear:
            self.spine_linear[target] = self.spine_linear.pop(path)
        self.toc = updated_toc
        return {"files_changed": len(pending)}

    def delete_resource(self, path: str) -> None:
        self._file(path)
        if path in {self.nav_path, self.ncx_path}:
            raise ValueError("Navigation resources cannot be deleted yet.")
        inbound = self.resource_references(path)
        if inbound:
            raise ValueError("Resource is still referenced by: " + ", ".join(inbound[:5]))
        package = self._package()
        manifest = package.find(f"{{{OPF}}}manifest")
        if manifest is None:
            raise ValueError("EPUB manifest is missing.")
        item = next((entry for entry in manifest if resolve_epub_href(
            posixpath.dirname(self.opf_path), entry.get("href", ""),
        ) == path), None)
        if item is None:
            raise ValueError("Resource is missing from the EPUB manifest.")
        manifest.remove(item)
        self._record()
        self.changes[self.opf_path] = _serialize(package)
        self.changes.pop(path, None)
        self.removed.add(path)
        self.files = [entry for entry in self.files if entry["path"] != path]

    def reorder_spine(self, paths: list[str]) -> None:
        if len(paths) != len(self.spine) or set(paths) != set(self.spine):
            raise ValueError(
                "Reading order must contain every existing spine item exactly once."
            )
        if paths != self.spine:
            self._record()
            self.spine = paths.copy()

    def set_spine(self, entries: list[dict[str, object]]) -> None:
        available = {
            str(item["path"]) for item in self.files
            if item["media_type"] in {"application/xhtml+xml", "text/html"}
        }
        paths = [entry.get("path") for entry in entries]
        if (
            not paths or len(paths) != len(set(paths))
            or not all(isinstance(path, str) and path in available for path in paths)
            or not all(isinstance(entry.get("linear"), bool) for entry in entries)
        ):
            raise ValueError("Reading order needs unique XHTML/HTML resources and linear flags.")
        linear = {str(entry["path"]): bool(entry["linear"]) for entry in entries}
        if paths != self.spine or linear != self.spine_linear:
            self._record()
            self.spine = [str(path) for path in paths]
            self.spine_linear = linear

    def set_toc(self, entries: list[dict[str, object]]) -> None:
        if not self.nav_path and not self.ncx_path:
            raise ValueError("This EPUB has no editable navigation document.")
        if len(entries) > 5000:
            raise ValueError("Table of contents is too large.")
        valid_files = {
            str(item["path"])
            for item in self.files
            if item["media_type"] in {"application/xhtml+xml", "text/html"}
        }
        normalized: list[dict[str, object]] = []
        target_ids: dict[str, set[str]] = {}
        for index, entry in enumerate(entries):
            label = str(entry.get("label", "")).strip()
            href = str(entry.get("href", "")).strip()
            depth = entry.get("depth", 0)
            target = href.split("#", 1)[0]
            if (
                not label
                or (href and target not in valid_files)
                or (not href and not self.nav_path)
                or not isinstance(depth, int)
                or depth < 0
                or depth > 8
                or (index == 0 and depth != 0)
                or (index > 0 and depth > int(normalized[-1]["depth"]) + 1)
            ):
                raise ValueError(f"Invalid table-of-contents entry at row {index + 1}.")
            fragment = unquote(href.partition("#")[2])
            if fragment and not fragment.startswith("epubcfi("):
                if target not in target_ids:
                    target_ids[target] = _document_ids(self._bytes(target))
                if fragment not in target_ids[target]:
                    raise ValueError(
                        f"Table-of-contents target is missing at row {index + 1}: {href}"
                    )
            normalized.append({"label": label, "href": href, "depth": depth})
        for index, entry in enumerate(normalized):
            if not entry["href"] and (
                index + 1 == len(normalized)
                or int(normalized[index + 1]["depth"]) <= int(entry["depth"])
            ):
                raise ValueError(f"Directory group needs a child at row {index + 1}.")
        if normalized != self.toc:
            pending = self._toc_updates(normalized)
            self._record()
            self.toc = normalized
            self.changes.update(pending)

    def generate_toc(
        self, patterns: list[str] | None = None, preview_only: bool = False,
        source: str = "headings",
    ) -> dict[str, object]:
        if not self.nav_path and not self.ncx_path:
            raise ValueError("This EPUB has no editable navigation document.")
        if source not in {"headings", "files", "xpath"} or (source == "files" and patterns is not None):
            raise ValueError("Choose headings, XPath or reading-order files for directory generation.")
        if source == "xpath" and patterns is None:
            raise ValueError("Provide at least one XPath directory expression.")
        if patterns is not None and (
            not patterns or len(patterns) > 8 or not any(patterns)
            or any(not isinstance(pattern, str) or len(pattern) > 500 for pattern in patterns)
        ):
            raise ValueError("Provide one to eight directory level patterns under 500 characters.")
        try:
            compiled = [
                (etree.XPath(pattern, namespaces={"h": XHTML, "x": XHTML, "xhtml": XHTML, "epub": "http://www.idpf.org/2007/ops"}, regexp=False)
                 if source == "xpath" else _search_pattern(pattern, False, True)) if pattern else None
                for pattern in patterns or []
            ]
        except etree.XPathError as exc:
            raise ValueError(f"Invalid directory XPath: {exc}") from exc
        deadline = time.monotonic() + SEARCH_TIMEOUT_SECONDS
        entries: list[tuple[int, str, str]] = []
        pending: dict[str, bytes] = {}
        approximate_targets = 0
        media_by_path = {str(item["path"]): str(item["media_type"]) for item in self.files}
        for path in dict.fromkeys(self.spine):
            if path == self.nav_path or media_by_path.get(path) not in {"application/xhtml+xml", "text/html"}:
                continue
            data = self._bytes(path)
            if len(data) > MAX_TEXT_BYTES:
                raise ValueError(f"Chapter exceeds the 4 MB editor limit: {path}")
            media = media_by_path[path]
            malformed = False
            try:
                root = _xml(data) if media == "application/xhtml+xml" else lxml_html.fromstring(
                    data, parser=lxml_html.HTMLParser(no_network=True)
                )
            except etree.XMLSyntaxError:
                malformed = True
                try:
                    root = lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True))
                except etree.ParserError as exc:
                    raise ValueError(f"Cannot read headings from malformed chapter: {path}") from exc
            except etree.ParserError as exc:
                raise ValueError(f"Cannot read headings from chapter: {path}") from exc
            if source == "files":
                heading = next((node for node in root.iter() if isinstance(node.tag, str)
                                and etree.QName(node).localname.lower() in {"h1", "h2"}), None)
                title = next((node for node in root.iter() if isinstance(node.tag, str)
                              and etree.QName(node).localname.lower() == "title"), None)
                label = next((" ".join("".join(node.itertext()).split()) for node in (heading, title)
                              if node is not None and " ".join("".join(node.itertext()).split())), "")
                label = label[:200] or unquote(posixpath.splitext(posixpath.basename(path))[0])
                if len(entries) >= 5000:
                    raise ValueError("Table of contents is too large.")
                entries.append((1, label, path))
                continue
            body = next(
                (node for node in root.iter() if isinstance(node.tag, str) and etree.QName(node).localname.lower() == "body"),
                None,
            )
            if body is None:
                continue
            ids = _document_ids(data)
            xpath_levels: dict[etree._Element, int] = {}
            if source == "xpath":
                body_nodes = set(body.iter())
                for index, expression in enumerate(compiled):
                    if expression is None:
                        continue
                    if time.monotonic() > deadline:
                        raise ValueError("Directory extraction timed out; narrow the XPath expressions.")
                    try:
                        nodes = expression(root)
                    except etree.XPathError as exc:
                        raise ValueError(f"Invalid directory XPath: {exc}") from exc
                    if not isinstance(nodes, list) or any(not isinstance(node, etree._Element) or not isinstance(node.tag, str) or node not in body_nodes or node is body for node in nodes):
                        raise ValueError("Directory XPath must select elements inside the chapter body.")
                    for node in nodes:
                        xpath_levels.setdefault(node, index + 1)
            used_targets: set[str] = set()
            changed = False
            used_chapter_start = False
            for node in body.iter():
                if not isinstance(node.tag, str):
                    continue
                tag = etree.QName(node).localname.lower()
                if source == "xpath" and node not in xpath_levels:
                    continue
                if source != "xpath" and tag not in {"h1", "h2", "h3", "h4", "h5", "h6"} | ({"p", "div"} if patterns is not None else set()):
                    continue
                if source != "xpath" and tag == "div" and any(
                    isinstance(child.tag, str) and etree.QName(child).localname.lower() in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6"}
                    for child in node
                ):
                    continue
                if source != "xpath" and tag == "p" and any(
                    isinstance(ancestor.tag, str)
                    and etree.QName(ancestor).localname.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}
                    for ancestor in node.iterancestors()
                ):
                    continue
                label = " ".join("".join(node.itertext()).split())
                if not label or len(label) > 200:
                    continue
                level = xpath_levels[node] if source == "xpath" else (int(tag[1]) if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} else 1)
                if patterns is not None and source != "xpath":
                    matched = False
                    for index, pattern in enumerate(compiled):
                        if pattern is None:
                            continue
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ValueError("Directory extraction timed out; narrow the patterns.")
                        try:
                            hit = pattern.search(label, timeout=remaining)
                        except TimeoutError as exc:
                            raise ValueError("Directory extraction timed out; narrow the patterns.") from exc
                        if hit:
                            if hit.start() == hit.end():
                                raise ValueError("Zero-width directory matches are not supported.")
                            label = hit.groupdict().get("title") or (hit.group(1) if hit.lastindex else label)
                            label = " ".join(label.split())
                            level = index + 1
                            matched = True
                            break
                    if not matched or not label:
                        continue
                if len(entries) >= 5000:
                    raise ValueError("Table of contents is too large.")
                identifier = node.get("id", "")
                if not identifier or identifier in used_targets:
                    if malformed:
                        if used_chapter_start:
                            continue
                        used_chapter_start = True
                        entries.append((level, label, path))
                        approximate_targets += 1
                        continue
                    index = len(entries) + 1
                    identifier = f"transoria-heading-{index}"
                    while identifier in ids:
                        index += 1
                        identifier = f"transoria-heading-{index}"
                    node.set("id", identifier)
                    ids.add(identifier)
                    changed = True
                used_targets.add(identifier)
                entries.append((level, label, f"{path}#{quote(identifier, safe='-._~')}"))
            if changed:
                pending[path] = etree.tostring(
                    root.getroottree() if media == "application/xhtml+xml" else root,
                    encoding="utf-8", xml_declaration=media == "application/xhtml+xml",
                    method="xml" if media == "application/xhtml+xml" else "html",
                )
        if not entries:
            if patterns is not None:
                raise ValueError("No table-of-contents entries matched these patterns in the reading order.")
            raise ValueError("No chapter headings were found in the reading order.")
        base_level = min(level for level, _, _ in entries)
        generated: list[dict[str, object]] = []
        for level, label, href in entries:
            depth = min(8, level - base_level)
            depth = min(depth, int(generated[-1]["depth"]) + 1) if generated else 0
            generated.append({"label": label, "href": href, "depth": depth})
        if not preview_only and (pending or generated != self.toc):
            pending.update(self._toc_updates(generated))
            self._record()
            self.changes.update(pending)
            self.toc = generated
        result: dict[str, object] = {
            "generated_entries": len(generated), "approximate_targets": approximate_targets,
        }
        if preview_only:
            result["entries"] = generated
        return result

    def generate_toc_page(self, title: str) -> str:
        title = title.strip()
        if not title or len(title) > 200 or not self.toc:
            raise ValueError("A title and at least one TOC entry are required.")
        path = posixpath.join(posixpath.dirname(self.opf_path), "Text/transoria_contents.xhtml")
        self._check_new_path(path)
        valid = {str(item["path"]) for item in self.files}
        for entry in self.toc:
            if entry["href"] and str(entry["href"]).split("#", 1)[0] not in valid:
                raise ValueError("TOC contains a missing resource; repair it before generating a page.")
        root = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML})
        head = etree.SubElement(root, f"{{{XHTML}}}head")
        etree.SubElement(head, f"{{{XHTML}}}title").text = title
        body = etree.SubElement(root, f"{{{XHTML}}}body")
        etree.SubElement(body, f"{{{XHTML}}}h1").text = title
        top = etree.SubElement(body, f"{{{XHTML}}}ol")
        levels = [top]
        for entry in self.toc:
            depth = int(entry["depth"])
            if depth < 0 or depth > 8 or depth > len(levels):
                raise ValueError("TOC nesting is invalid.")
            while len(levels) > depth + 1:
                levels.pop()
            if depth + 1 > len(levels):
                if not len(levels[-1]):
                    raise ValueError("TOC nesting is invalid.")
                levels.append(etree.SubElement(levels[-1][-1], f"{{{XHTML}}}ol"))
            li = etree.SubElement(levels[-1], f"{{{XHTML}}}li")
            link = etree.SubElement(li, f"{{{XHTML}}}{'a' if entry['href'] else 'span'}")
            if entry["href"]:
                link.set("href", _relative_href(path, str(entry["href"])))
            link.text = str(entry["label"])
        self.add_resource(path, _serialize(root), "application/xhtml+xml", in_spine=True)
        self.spine.remove(path)
        self.spine.insert(0, path)
        return path

    def search(
        self, query: str, paths: list[str], case_sensitive: bool = False,
        regular_expression: bool = False,
        selection: dict[str, object] | None = None,
        ignore_markup: bool = False,
    ) -> list[dict[str, object]]:
        if not query:
            return []
        if len(query) > 2000:
            raise ValueError("Search text is too long.")
        pattern = _search_pattern(query, case_sensitive, regular_expression)
        deadline = time.monotonic() + SEARCH_TIMEOUT_SECONDS
        results: list[dict[str, object]] = []
        for path in dict.fromkeys(paths):
            item = self._file(path, editable=True)
            if int(item["size"]) > MAX_TEXT_BYTES and path not in self.changes:
                continue
            data = self._bytes(path)
            if len(data) > MAX_TEXT_BYTES:
                continue
            fingerprint = hashlib.sha256(data).hexdigest()
            content = _editor_text(data, str(item["media_type"]))
            start, end = _selection_bounds(path, content, selection)
            visible = None
            if ignore_markup:
                if item["media_type"] not in {"application/xhtml+xml", "text/html"}:
                    continue
                from transoria.tools.epub_text_search import VisibleText
                visible = VisibleText(content)
                searchable = visible.text
            else:
                searchable = content
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Search timed out; narrow the scope or pattern.")
            try:
                for match in pattern.finditer(searchable, pos=0 if visible else start, endpos=len(searchable) if visible else end, timeout=remaining):
                    if match.start() == match.end():
                        raise ValueError("Zero-width search matches are not supported.")
                    left, right = visible.source_range(match.start(), match.end()) if visible else (match.start(), match.end())
                    if left < start or right > end:
                        continue
                    results.append(
                        {
                            "path": path,
                            "start": left,
                            "end": right,
                            "ignore_markup": ignore_markup,
                            "fingerprint": fingerprint,
                            "excerpt": searchable[
                                max(0, match.start() - 45) : min(
                                    len(searchable), match.end() + 65
                                )
                            ].replace("\n", " "),
                        }
                    )
                    if len(results) >= 5000:
                        return results
            except TimeoutError as exc:
                raise ValueError("Search timed out; narrow the scope or pattern.") from exc
        return results

    def replace(
        self,
        query: str,
        replacement: str,
        paths: list[str],
        case_sensitive: bool = False,
        expected_count: int | None = None,
        regular_expression: bool = False,
        selection: dict[str, object] | None = None,
        expected_fingerprints: dict[str, str] | None = None,
        ignore_markup: bool = False,
    ) -> dict[str, object]:
        pending, total, _, _ = self._replacement_plan(
            query, replacement, paths, case_sensitive, regular_expression,
            selection, expected_fingerprints, ignore_markup,
        )
        if expected_count is not None and total != expected_count:
            raise ValueError("Search results changed; search again before replacing.")
        if pending:
            self._apply_changes(pending)
        return {"replacements": total, "files_changed": len(pending)}

    def preview_replace(
        self, query: str, replacement: str, paths: list[str],
        case_sensitive: bool = False, regular_expression: bool = False,
        selection: dict[str, object] | None = None,
        ignore_markup: bool = False,
    ) -> dict[str, object]:
        pending, total, samples, fingerprints = self._replacement_plan(
            query, replacement, paths, case_sensitive, regular_expression,
            selection, None, ignore_markup,
        )
        return {
            "replacements": total, "files_changed": len(pending),
            "samples": samples, "fingerprints": fingerprints,
        }

    def _replacement_plan(
        self, query: str, replacement: str, paths: list[str],
        case_sensitive: bool, regular_expression: bool,
        selection: dict[str, object] | None,
        expected_fingerprints: dict[str, str] | None,
        ignore_markup: bool = False,
    ) -> tuple[dict[str, bytes], int, list[dict[str, object]], dict[str, str]]:
        if not query:
            raise ValueError("Search text is required.")
        if len(query) > 2000:
            raise ValueError("Search text is too long.")
        pattern = _search_pattern(query, case_sensitive, regular_expression)
        deadline = time.monotonic() + SEARCH_TIMEOUT_SECONDS
        pending: dict[str, bytes] = {}
        total = 0
        samples: list[dict[str, object]] = []
        fingerprints: dict[str, str] = {}
        for path in dict.fromkeys(paths):
            item = self._file(path, editable=True)
            if int(item["size"]) > MAX_TEXT_BYTES and path not in self.changes:
                raise ValueError(f"Resource exceeds the editor limit: {path}")
            data = self._bytes(path)
            if len(data) > MAX_TEXT_BYTES:
                raise ValueError(f"Resource exceeds the editor limit: {path}")
            fingerprint = hashlib.sha256(data).hexdigest()
            if expected_fingerprints is not None and expected_fingerprints.get(path) != fingerprint:
                raise ValueError("Search results changed; preview replacement again.")
            fingerprints[path] = fingerprint
            content = _editor_text(data, str(item["media_type"]))
            start, end = _selection_bounds(path, content, selection)
            encoding = _decode(data)[1]
            visible = None
            if ignore_markup:
                if item["media_type"] not in {"application/xhtml+xml", "text/html"}:
                    continue
                from transoria.tools.epub_text_search import VisibleText
                visible = VisibleText(content)
            searchable = visible.text if visible else content
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Replacement timed out; narrow the scope or pattern.")
            pieces: list[str] = []
            cursor = start
            count = 0
            edits: list[tuple[int, int, str]] = []
            try:
                for match in pattern.finditer(searchable, pos=0 if visible else start, endpos=len(searchable) if visible else end, timeout=remaining):
                    if match.start() == match.end():
                        raise ValueError("Zero-width search matches are not supported.")
                    after = match.expand(replacement) if regular_expression else replacement
                    left, right = visible.source_range(match.start(), match.end()) if visible else (match.start(), match.end())
                    if left < start or right > end:
                        continue
                    if visible:
                        edits.extend(visible.replacement_edits(match.start(), match.end(), after))
                    if not visible:
                        pieces.extend((content[cursor:match.start()], after))
                        cursor = match.end()
                    count += 1
                    if len(samples) < 30:
                        samples.append({
                            "path": path, "start": left,
                            "before": match.group(), "after": after,
                        })
            except TimeoutError as exc:
                raise ValueError("Replacement timed out; narrow the scope or pattern.") from exc
            except (regex.error, IndexError, KeyError) as exc:
                raise ValueError(f"Invalid replacement expression: {exc}") from exc
            if count:
                if visible:
                    updated = content
                    for left, right, after in reversed(edits):
                        updated = updated[:left] + after + updated[right:]
                    encoded = _encode(updated, encoding)
                else:
                    pieces.append(content[cursor:end])
                    updated = "".join(pieces)
                    encoded = _encode(content[:start] + updated + content[end:], encoding)
                if len(encoded) > MAX_TEXT_BYTES:
                    raise ValueError(f"Replacement exceeds the editor limit: {path}")
                pending[path] = encoded
                total += count
        return pending, total, samples, fingerprints

    def replace_match(
        self,
        path: str,
        start: int,
        end: int,
        query: str,
        replacement: str,
        case_sensitive: bool = False,
        regular_expression: bool = False,
        expected_fingerprint: str | None = None,
        ignore_markup: bool = False,
    ) -> int:
        item = self._file(path, editable=True)
        data = self._bytes(path)
        if expected_fingerprint is not None and hashlib.sha256(data).hexdigest() != expected_fingerprint:
            raise ValueError("Search result changed; search again before replacing.")
        content = _editor_text(data, str(item["media_type"]))
        encoding = _decode(data)[1]
        if not 0 <= start < end <= len(content):
            raise ValueError("Search result is no longer current; search again.")
        pattern = _search_pattern(query, case_sensitive, regular_expression)
        if ignore_markup:
            result = self.replace(query, replacement, [path], case_sensitive, 1, regular_expression, {"path": path, "start": start, "end": end}, {path: hashlib.sha256(data).hexdigest()}, True)
            if result["replacements"] != 1:
                raise ValueError("Search result is no longer current; search again.")
            return end + len(_editor_text(self._bytes(path), str(item["media_type"]))) - len(content)
        try:
            match = pattern.match(content, pos=start, timeout=SEARCH_TIMEOUT_SECONDS)
            if match is None or match.end() != end or match.start() == match.end():
                raise ValueError("Search result is no longer current; search again.")
            substituted = match.expand(replacement) if regular_expression else replacement
        except TimeoutError as exc:
            raise ValueError("Replacement timed out; narrow the scope or pattern.") from exc
        except (regex.error, IndexError, KeyError) as exc:
            raise ValueError(f"Invalid replacement expression: {exc}") from exc
        updated = _encode(content[:start] + substituted + content[end:], encoding)
        if len(updated) > MAX_TEXT_BYTES:
            raise ValueError(f"Replacement exceeds the editor limit: {path}")
        self._apply_changes({path: updated})
        return start + len(substituted)

    def preview(
        self, path: str, draft_path: str = "", draft_content: str | None = None
    ) -> str:
        item = self._file(path)
        if item["media_type"] not in {"application/xhtml+xml", "text/html", "image/svg+xml"}:
            raise ValueError("Preview is available for XHTML/HTML/SVG resources only.")
        drafts: dict[str, bytes] = {}
        if draft_path and draft_content is not None:
            self._file(draft_path, editable=True)
            if len(draft_content.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ValueError("Resource exceeds the 4 MB editor limit.")
            drafts[draft_path] = _encode(
                draft_content, _decode(self._bytes(draft_path))[1]
            )
        preview_cache: dict[str, bytes] = {}
        remaining = MAX_PREVIEW_BYTES

        def preview_bytes(resource_path: str) -> bytes:
            nonlocal remaining
            if resource_path not in preview_cache:
                if resource_path not in drafts and int(self._file(resource_path)["size"]) > remaining:
                    raise ValueError("Preview resources exceed the 48 MB limit.")
                data = drafts[resource_path] if resource_path in drafts else self._bytes(resource_path)
                if len(data) > remaining:
                    raise ValueError("Preview resources exceed the 48 MB limit.")
                remaining -= len(data)
                preview_cache[resource_path] = data
            return preview_cache[resource_path]

        if int(item["size"]) > MAX_PREVIEW_BYTES and path not in drafts:
            raise ValueError("Preview resources exceed the 48 MB limit.")
        markup_bytes = preview_bytes(path)
        if len(markup_bytes) <= MAX_TEXT_BYTES:
            formatted = _editor_text(markup_bytes, str(item["media_type"]))
            formatted = re.sub(r'(<\?xml[^>]*encoding\s*=\s*)[\'\"][^\'\"]+[\'\"]', r'\1"utf-8"', formatted, count=1)
            markup_bytes = formatted.encode("utf-8")
        root = _preview_root(markup_bytes, html_document=item["media_type"] == "text/html")
        from transoria.tools.epub_rendition import rendition
        rendering = rendition(_xml(drafts.get(self.opf_path, self._bytes(self.opf_path))), root, self.opf_path, path)
        if item["media_type"] == "image/svg+xml":
            svg = root
            root = etree.Element("html")
            etree.SubElement(root, "head")
            etree.SubElement(root, "body", style="margin:0").append(svg)
        for node in list(root.iter()):
            local = (
                _local_name(node.tag).lower()
            )
            if local:
                # Browser HTML parsing does not recognize prefixed XHTML element names.
                node.tag = _local_name(node.tag)
            if local:
                node.set("data-transoria-line", str(node.sourceline) if node.sourceline is not None else node.get("data-transoria-line", "1"))
                node.attrib.pop("data-transoria-target", None)
            if local in {"script", "iframe", "object", "embed", "form", "base"}:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                continue
            if local == "style" and node.text:
                node.text = _inline_css(node.text, path, self, drafts, read_bytes=preview_bytes)
            if node.get("style"):
                node.set("style", _inline_css(node.get("style", ""), path, self, drafts, read_bytes=preview_bytes, declarations=True))
            for key in list(node.attrib):
                if _local_name(key).lower().startswith("on"):
                    del node.attrib[key]
            svg_href = "{http://www.w3.org/1999/xlink}href"
            if local == "image":
                for key in (svg_href, "xlink:href"):
                    value = node.attrib.pop(key, None)
                    if value is not None and not node.get("href"):
                        node.set("href", value)
            for attr in ("src", "href", "poster", svg_href):
                value = node.get(attr)
                if local == "a" and attr == "href" and value and value.startswith("#"):
                    node.set("data-transoria-target", path + value)
                if not value or value.startswith(("#", "data:")):
                    continue
                if urlsplit(value).scheme or value.startswith("//"):
                    node.attrib.pop(attr, None)
                    continue
                target = resolve_epub_href(posixpath.dirname(path), value)
                known = next(
                    (file for file in self.files if decode_epub_href(str(file["path"])) == decode_epub_href(target)), None
                )
                if known:
                    target = str(known["path"])
                if local == "a" and attr == "href" and known:
                    node.set("data-transoria-target", target + ("#" + value.partition("#")[2] if "#" in value else ""))
                if (
                    local == "link"
                    and attr == "href"
                    and known
                    and known["media_type"] == "text/css"
                ):
                    rel = node.get("rel", "").lower().split()
                    if "stylesheet" not in rel or "alternate" in rel or "disabled" in node.attrib:
                        node.getparent().remove(node)
                        break
                    css = _decode(preview_bytes(target))[0]
                    style = etree.Element("style")
                    if node.get("media"):
                        style.set("media", node.get("media"))
                    style.text = _inline_css(css, target, self, drafts, read_bytes=preview_bytes)
                    node.getparent().replace(node, style)
                    break
                if (
                    (attr in {"src", "poster"} or (local == "image" and attr in {"href", svg_href}))
                    and known
                    and str(known["media_type"]).startswith("image/")
                ):
                    data = preview_bytes(target)
                    node.set(
                        attr,
                        f"data:{known['media_type']};base64,{base64.b64encode(data).decode('ascii')}",
                    )
                elif attr in {"href", svg_href}:
                    node.set(attr, "#")
                else:
                    node.attrib.pop(attr, None)
        markup = etree.tostring(root, encoding="unicode", method="html")
        if len(markup.encode("utf-8")) > MAX_PREVIEW_BYTES:
            raise ValueError("Preview exceeds the 48 MB limit.")
        csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; font-src data:"
        fit_media = (
            "<style>img,svg,video{max-width:100%!important;"
            "max-height:calc(100vh - 24px)!important;"
            "object-fit:contain!important}"
            "html,body{max-width:100%;box-sizing:border-box;overflow-wrap:anywhere}</style>"
        )
        if rendering["layout"] == "pre-paginated":
            fit_media = ""
        return (
            f'<meta http-equiv="Content-Security-Policy" content="{html.escape(csp)}">'
            + f'<meta name="transoria-rendition" content="{html.escape(json.dumps(rendering))}">'
            + fit_media
            + markup
        )

    def save(self, output_path: str, overwrite: bool) -> dict[str, object]:
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".epub":
            raise ValueError("Output must end in .epub.")
        if _fingerprint(self.path) != self.fingerprint:
            raise ValueError("Source EPUB changed on disk; reopen it before saving.")
        if output.exists() and not overwrite:
            raise ValueError("Output exists; confirm overwrite first.")
        if not output.parent.is_dir():
            raise ValueError("Output folder does not exist.")
        pending = self.changes.copy()
        baseline = ContentSession.open(str(self.path))
        with zipfile.ZipFile(self.path) as source:
            opf = _xml(pending.get(self.opf_path, source.read(self.opf_path)))
            manifest = {
                item.get("id", ""): item
                for item in opf.findall(f".//{{{OPF}}}manifest/{{{OPF}}}item")
            }
            spine_node = opf.find(f".//{{{OPF}}}spine")
            if spine_node is None:
                raise ValueError("EPUB reading order is missing.")
            itemrefs = list(spine_node)
            ref_by_path: dict[str, etree._Element] = {}
            id_by_path: dict[str, str] = {}
            for item_id, item in manifest.items():
                resolved = resolve_epub_href(posixpath.dirname(self.opf_path), item.get("href", ""))
                entry = (
                    resolved if any(file["path"] == resolved for file in self.files)
                    else find_archive_entry_by_normalized_path(source, resolved)
                )
                if entry:
                    id_by_path[entry] = item_id
            path_by_id = {item_id: path for path, item_id in id_by_path.items()}
            for itemref in itemrefs:
                path = path_by_id.get(itemref.get("idref", ""), "")
                if not path or path in ref_by_path:
                    raise ValueError("Reading order contains unresolved or duplicate items.")
                ref_by_path[path] = itemref
            if set(self.spine) != set(self.spine_linear) or any(path not in id_by_path for path in self.spine):
                raise ValueError("Reading order no longer matches the package.")
            wanted_refs = []
            spine_changed = False
            for path in self.spine:
                itemref = ref_by_path.get(path)
                if itemref is None:
                    itemref = etree.Element(f"{{{OPF}}}itemref", idref=id_by_path[path])
                    spine_changed = True
                if (itemref.get("linear") != "no") != self.spine_linear[path]:
                    itemref.set("linear", "yes" if self.spine_linear[path] else "no")
                    spine_changed = True
                wanted_refs.append(itemref)
            if wanted_refs != itemrefs or spine_changed:
                for itemref in itemrefs:
                    spine_node.remove(itemref)
                for itemref in wanted_refs:
                    spine_node.append(itemref)
                pending[self.opf_path] = _serialize(opf)
            if self.toc != baseline.toc or self.nav_path != baseline.nav_path:
                if self.nav_path:
                    pending[self.nav_path] = _write_nav(
                        pending[self.nav_path] if self.nav_path in pending else source.read(self.nav_path),
                        self.nav_path,
                        self.toc,
                    )
                if self.ncx_path:
                    pending[self.ncx_path] = _write_ncx(
                        pending[self.ncx_path] if self.ncx_path in pending else source.read(self.ncx_path),
                        self.ncx_path,
                        self.toc,
                    )
            for path, data in pending.items():
                media = next(
                    (
                        str(item["media_type"])
                        for item in self.files
                        if item["path"] == path
                    ),
                    "",
                )
                if media in XML_TYPES | {"application/xhtml+xml"} or path == self.opf_path:
                    try:
                        _xml(data)
                    except etree.XMLSyntaxError as exc:
                        raise ValueError(f"Invalid XML in {path}: {exc}") from exc
            before_check = inspect_epub_structure(self.path)
            before_missing_fragments = _missing_toc_fragments(
                source, baseline.toc
            )
            fd, temp_name = tempfile.mkstemp(
                prefix=".epub-content-", suffix=".epub", dir=output.parent
            )
            os.close(fd)
            temp = Path(temp_name)
            try:
                with zipfile.ZipFile(temp, "w") as target:
                    for info in source.infolist():
                        if info.filename in self.removed:
                            continue
                        data = (
                            pending.pop(info.filename)
                            if info.filename in pending
                            else source.read(info.filename)
                        )
                        target.writestr(info, data)
                    for path, data in pending.items():
                        if any(item["path"] == path for item in self.files):
                            target.writestr(path, data)
                        else:
                            raise ValueError(f"Edited resource is missing from manifest: {path}")
                with zipfile.ZipFile(temp) as check_archive:
                    bad = check_archive.testzip()
                    if bad:
                        raise ValueError(f"EPUB archive corruption: {bad}")
                    added_missing_fragments = _missing_toc_fragments(
                        check_archive,
                        _read_toc(check_archive, self.nav_path, self.ncx_path, tolerant=True),
                    ) - before_missing_fragments
                    if added_missing_fragments:
                        raise ValueError(
                            "New broken table-of-contents targets: "
                            + ", ".join(sorted(added_missing_fragments)[:5])
                        )
                after_check = inspect_epub_structure(temp)
                if after_check["status"] == "failed":
                    raise ValueError(
                        f"EPUB validation failed: {after_check.get('error', '')}"
                    )
                before_missing = set(before_check.get("missing_entries", []))
                added_missing = (
                    set(after_check.get("missing_entries", [])) - before_missing
                )
                if added_missing:
                    raise ValueError(
                        f"New broken resource references: {', '.join(sorted(added_missing)[:5])}"
                    )
                if overwrite:
                    os.replace(temp, output)
                else:
                    try:
                        os.link(temp, output)
                    except FileExistsError as exc:
                        raise ValueError(
                            "Output exists; confirm overwrite first."
                        ) from exc
            finally:
                temp.unlink(missing_ok=True)
        reopened = ContentSession.open(str(output))
        self.path = reopened.path
        self.fingerprint = reopened.fingerprint
        self.files = reopened.files
        self.spine = reopened.spine
        self.spine_linear = reopened.spine_linear
        self.toc = reopened.toc
        self.nav_path = reopened.nav_path
        self.ncx_path = reopened.ncx_path
        self.changes.clear()
        self.removed.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.checkpoints.clear()
        self.dirty = False
        self.clean_snapshot = self._snapshot()
        return {"output_path": str(output), "structure_check": after_check}

    def validate(self) -> dict[str, object]:
        candidate = copy.copy(self)
        candidate.changes = self.changes.copy()
        candidate.removed = self.removed.copy()
        candidate.files = copy.deepcopy(self.files)
        candidate.spine = self.spine.copy()
        candidate.spine_linear = self.spine_linear.copy()
        candidate.toc = copy.deepcopy(self.toc)
        with tempfile.TemporaryDirectory(prefix="transoria-epub-check-") as folder:
            result = candidate.save(str(Path(folder) / "checked.epub"), overwrite=False)
        return {"structure_check": result["structure_check"]}


def _read_toc(
    archive: zipfile.ZipFile, nav_path: str, ncx_path: str,
    overrides: dict[str, bytes] | None = None,
    *, tolerant: bool = False,
) -> list[dict[str, object]]:
    malformed_nav: bytes | None = None
    if nav_path:
        data = overrides[nav_path] if overrides and nav_path in overrides else _package_bytes(archive, nav_path)
        try:
            root = _xml(data)
        except etree.XMLSyntaxError:
            if not tolerant:
                raise
            malformed_nav = data
            root = etree.Element("html")
        nav = next((n for n in root.iter() if _is_toc(n)), None)
        if nav is not None:
            ol = next(
                (
                    n
                    for n in nav
                    if isinstance(n.tag, str) and etree.QName(n).localname == "ol"
                ),
                None,
            )
            if ol is not None:
                return _toc_from_nav(ol, nav_path, archive)
    if ncx_path:
        try:
            root = _xml(overrides[ncx_path] if overrides and ncx_path in overrides else _package_bytes(archive, ncx_path))
        except etree.XMLSyntaxError:
            if not tolerant:
                raise
            root = etree.Element("ncx")
        nav_map = next((n for n in root.iter() if _local_name(n.tag) == "navMap"), None)
        if nav_map is not None:
            result: list[dict[str, object]] = []

            def walk(parent: etree._Element, depth: int) -> None:
                for point in parent:
                    if _local_name(point.tag) != "navPoint":
                        continue
                    label = "".join(
                        point.xpath(
                            "./*[local-name()='navLabel']/*[local-name()='text']/text()"
                        )
                    )
                    content = next((n for n in point if _local_name(n.tag) == "content"), None)
                    href = content.get("src", "") if content is not None else ""
                    result.append(
                        {
                            "label": label,
                            "href": _absolute_href(ncx_path, href, archive),
                            "depth": depth,
                        }
                    )
                    walk(point, depth + 1)

            walk(nav_map, 0)
            return result
    if malformed_nav is not None:
        try:
            root = lxml_html.fromstring(_decode(malformed_nav)[0])
            nav = next((n for n in root.iter() if _is_toc(n)), None)
            ol = next((n for n in nav if _local_name(n.tag) == "ol"), None) if nav is not None else None
            if ol is not None:
                return _toc_from_nav(ol, nav_path, archive)
        except (ValueError, etree.ParserError):
            pass
    return []


def _toc_from_nav(
    ol: etree._Element, nav_path: str, archive: zipfile.ZipFile
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []

    def walk(parent: etree._Element, depth: int) -> None:
        for li in parent:
            if _local_name(li.tag) != "li":
                continue
            link = next(
                (
                    n
                    for n in li
                    if isinstance(n.tag, str)
                    and _local_name(n.tag) in {"a", "span"}
                ),
                None,
            )
            if link is not None:
                result.append(
                    {
                        "label": " ".join("".join(link.itertext()).split()),
                        "href": _absolute_href(nav_path, link.get("href", ""), archive) if link.get("href") else "",
                        "depth": depth,
                    }
                )
            nested = next(
                (
                    n
                    for n in li
                    if _local_name(n.tag) == "ol"
                ),
                None,
            )
            if nested is not None:
                walk(nested, depth + 1)

    walk(ol, 0)
    return result


def _absolute_href(base: str, href: str, archive: zipfile.ZipFile) -> str:
    path = resolve_epub_href(posixpath.dirname(base), href)
    path = find_archive_entry_by_normalized_path(archive, path) or path
    fragment = href.split("#", 1)[1] if "#" in href else ""
    return path + (f"#{fragment}" if fragment else "")


def _relative_href(base: str, href: str) -> str:
    path, sep, fragment = href.partition("#")
    relative = posixpath.relpath(path, posixpath.dirname(base) or ".")
    return quote(relative, safe="/-._~") + (f"#{fragment}" if sep else "")


def _write_nav(data: bytes, path: str, entries: list[dict[str, object]]) -> bytes:
    root = _xml(data)
    nav = next(
        (
            n
            for n in root.iter()
            if isinstance(n.tag, str)
            and _is_toc(n)
        ),
        None,
    )
    if nav is None:
        raise ValueError("EPUB navigation section is missing.")
    old = next(
        (n for n in nav if isinstance(n.tag, str) and etree.QName(n).localname == "ol"),
        None,
    )
    if old is None:
        raise ValueError("EPUB navigation list is missing.")
    index = list(nav).index(old)
    originals: dict[tuple[str, str], list[etree._Element]] = {}
    for li in old.iter():
        if _local_name(li.tag) != "li":
            continue
        link = next((n for n in li if _local_name(n.tag) in {"a", "span"}), None)
        if link is not None:
            originals.setdefault((link.get("href", ""), " ".join("".join(link.itertext()).split())), []).append(li)
    nav.remove(old)
    top = copy.deepcopy(old)
    for child in list(top):
        top.remove(child)
    nav.insert(index, top)
    levels = [top]
    for entry in entries:
        depth = int(entry["depth"])
        while len(levels) > depth + 1:
            levels.pop()
        if depth + 1 > len(levels):
            nested = etree.SubElement(levels[-1][-1], f"{{{XHTML}}}ol")
            levels.append(nested)
        href = _relative_href(path, str(entry["href"])) if entry["href"] else ""
        matches = originals.get((href, str(entry["label"])), [])
        if not matches:
            matches = next((items for (target, _), items in originals.items() if target == href and items), [])
        original = matches.pop(0) if matches else None
        li = copy.deepcopy(original) if original is not None else etree.Element(f"{{{XHTML}}}li")
        for child in list(li):
            if _local_name(child.tag) == "ol":
                li.remove(child)
        link = next((n for n in li if _local_name(n.tag) in {"a", "span"}), None)
        if link is None:
            link = etree.SubElement(li, f"{{{XHTML}}}{'a' if href else 'span'}")
        link.tag = f"{{{XHTML}}}{'a' if href else 'span'}"
        if href:
            link.set("href", href)
        else:
            link.attrib.pop("href", None)
        if " ".join("".join(link.itertext()).split()) != str(entry["label"]):
            for child in list(link):
                link.remove(child)
            link.text = str(entry["label"])
        levels[-1].append(li)
    return _serialize(root)


def _write_ncx(data: bytes, path: str, entries: list[dict[str, object]]) -> bytes:
    root = _xml(data)
    nav_map = next((n for n in root.iter() if _local_name(n.tag) == "navMap"), None)
    if nav_map is None:
        raise ValueError("NCX navigation map is missing.")
    namespace = etree.QName(nav_map).namespace

    def tag(name: str) -> str:
        return f"{{{namespace}}}{name}" if namespace else name

    originals: dict[tuple[str, str], list[etree._Element]] = {}
    for point in nav_map.iter(tag("navPoint")):
        content = next((n for n in point if _local_name(n.tag) == "content"), None)
        label = "".join(point.xpath("./*[local-name()='navLabel']/*[local-name()='text']/text()"))
        originals.setdefault((content.get("src", "") if content is not None else "", label), []).append(point)
    used_ids = {n.get("id") for n in root.iter() if n.get("id")}
    for point in list(nav_map):
        if _local_name(point.tag) == "navPoint":
            nav_map.remove(point)
    levels = [nav_map]
    for index, entry in enumerate(entries):
        depth = int(entry["depth"])
        while len(levels) > depth + 1:
            levels.pop()
        if depth + 1 > len(levels):
            levels.append(levels[-1][-1])
        href = str(entry["href"])
        if not href:
            href = next((str(child["href"]) for child in entries[index + 1:] if child["href"]), "")
        relative = _relative_href(path, href)
        matches = originals.get((relative, str(entry["label"])), [])
        if not matches and entry["href"]:
            matches = next((items for (target, _), items in originals.items() if target == relative and items), [])
        original = matches.pop(0) if matches else None
        point = etree.SubElement(levels[-1], tag("navPoint"), attrib=dict(original.attrib) if original is not None else {})
        if not point.get("id"):
            identifier = f"transoria-nav-{index+1}"
            while identifier in used_ids:
                identifier += "-new"
            point.set("id", identifier)
            used_ids.add(identifier)
        point.set("playOrder", str(index + 1))
        label = etree.SubElement(point, tag("navLabel"))
        etree.SubElement(label, tag("text")).text = str(entry["label"])
        etree.SubElement(point, tag("content"), src=relative)
    return _serialize(root)


def _inline_css(
    css: str, base: str, session: ContentSession,
    drafts: dict[str, bytes] | None = None, visited: frozenset[str] = frozenset(),
    read_bytes: Callable[[str], bytes] | None = None,
    *, declarations: bool = False,
) -> str:
    drafts = drafts or {}
    read_bytes = read_bytes or session._bytes
    if base in visited or len(visited) >= 64:
        return ""
    visited = visited | {base}
    url_bytes = 0

    def resource(href: str) -> dict[str, object] | None:
        target = resolve_epub_href(posixpath.dirname(base), href)
        return next((file for file in session.files if decode_epub_href(str(file["path"])) == decode_epub_href(target)), None)

    def import_css(rule) -> str:
        tokens = [token for token in rule.prelude if token.type not in {"whitespace", "comment"}]
        if not tokens:
            return ""
        first = tokens.pop(0)
        href = first.value if first.type in {"string", "url"} else ""
        if first.type == "function" and first.lower_name == "url":
            args = [token for token in first.arguments if token.type not in {"whitespace", "comment"}]
            href = args[0].value if len(args) == 1 and args[0].type == "string" else ""
        if not href or urlsplit(href).scheme or href.startswith("//"):
            return ""
        item = resource(href)
        if not item or item["media_type"] != "text/css":
            return ""
        path = str(item["path"])
        if path in visited or len(read_bytes(path)) > MAX_TEXT_BYTES:
            return ""
        imported = _inline_css(_decode(read_bytes(path))[0], path, session, drafts, visited, read_bytes)
        # Separate sheets retain import ordering, namespace scope and cascade layers.
        encoded = base64.b64encode(imported.encode("utf-8")).decode("ascii")
        if len(encoded) > MAX_PREVIEW_BYTES:
            raise ValueError("Expanded preview CSS exceeds the 48 MB limit.")
        conditions = " ".join(token.serialize() for token in tokens)
        return f'@import url("data:text/css;base64,{encoded}") {conditions};'

    def data_url(raw: str) -> str:
        nonlocal url_bytes
        if (
            not raw
            or raw.startswith(("data:", "#"))
            or urlsplit(raw).scheme
            or raw.startswith("//")
        ):
            return raw if raw.startswith(("data:", "#")) else ""
        item = resource(raw)
        if not item:
            return ""
        path = str(item["path"])
        data = read_bytes(path)
        media = (
            str(item["media_type"])
            or mimetypes.guess_type(path)[0]
            or "application/octet-stream"
        )
        if media.startswith("font/") or "font" in media or media in {
            "application/vnd.ms-opentype", "application/x-font-ttf",
            "application/x-font-otf", "application/octet-stream",
        }:
            media = mimetypes.guess_type(path)[0] or media
            data = _preview_font_bytes(session, path, data)
        fragment = "#" + raw.partition("#")[2] if "#" in raw else ""
        url_bytes += 4 * ((len(data) + 2) // 3) + len(fragment) + len(media) + 16
        if url_bytes > MAX_PREVIEW_BYTES:
            raise ValueError("Expanded preview CSS exceeds the 48 MB limit.")
        return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}{fragment}"

    def urls(tokens) -> None:
        for token in tokens:
            if token.type == "url":
                token.value = data_url(token.value)
                token.representation = 'url("' + token.value.replace('"', '\\"') + '")'
            elif token.type == "function":
                if token.lower_name == "url":
                    args = [arg for arg in token.arguments if arg.type not in {"whitespace", "comment"}]
                    raw = args[0].value if len(args) == 1 and args[0].type == "string" else ""
                    token.arguments = tinycss2.parse_component_value_list(json.dumps(data_url(raw)))
                else:
                    urls(token.arguments)
            elif hasattr(token, "content") and token.content is not None:
                urls(token.content)

    aliases = {"-epub-writing-mode": "writing-mode", "-webkit-writing-mode": "writing-mode", "-ms-writing-mode": "writing-mode", "-epub-text-orientation": "text-orientation",
               "-epub-text-combine-upright": "text-combine-upright", "-epub-ruby-position": "ruby-position",
               "-epub-text-combine": "text-combine-upright"}

    def rules(nodes) -> str:
        result: list[str] = []
        size = 0
        for node in nodes:
            if node.type == "error":
                continue
            if node.type == "at-rule" and node.lower_at_keyword == "import":
                imported = import_css(node)
                size += len(imported)
                if size > MAX_PREVIEW_BYTES:
                    raise ValueError("Expanded preview CSS exceeds the 48 MB limit.")
                result.append(imported)
                continue
            if node.type in {"qualified-rule", "at-rule"} and node.content is not None:
                node.content = tinycss2.parse_component_value_list(rules(tinycss2.parse_blocks_contents(node.content)))
            if node.type == "declaration":
                urls(node.value)
                result.append(node.serialize() + ";")
                if node.lower_name in aliases:
                    alias = copy.copy(node)
                    alias.name = alias.lower_name = aliases[node.lower_name]
                    if node.lower_name == "-epub-text-combine" and tinycss2.serialize(node.value).strip() == "horizontal":
                        alias.value = tinycss2.parse_component_value_list("all")
                    result.append(alias.serialize() + ";")
                if node.lower_name in {"writing-mode", "-epub-writing-mode", "-webkit-writing-mode", "-ms-writing-mode"}:
                    legacy = {"tb": "vertical-rl", "tb-rl": "vertical-rl", "tb-lr": "vertical-lr", "lr": "horizontal-tb", "lr-tb": "horizontal-tb", "rl": "horizontal-tb", "rl-tb": "horizontal-tb"}.get(tinycss2.serialize(node.value).strip().lower())
                    if legacy:
                        alias = copy.copy(node)
                        alias.name = alias.lower_name = "writing-mode"
                        alias.value = tinycss2.parse_component_value_list(legacy)
                        result.append(alias.serialize() + ";")
            else:
                result.append(node.serialize())
            size += len(result[-1])
            if size > MAX_PREVIEW_BYTES:
                raise ValueError("Expanded preview CSS exceeds the 48 MB limit.")
        return "".join(result)

    return rules(tinycss2.parse_blocks_contents(css) if declarations else tinycss2.parse_stylesheet(css))


def _preview_font_bytes(session: ContentSession, path: str, data: bytes) -> bytes:
    with zipfile.ZipFile(session.path) as archive:
        if "META-INF/encryption.xml" not in archive.namelist():
            return data
        try:
            encryption = _xml(archive.read("META-INF/encryption.xml"))
        except etree.XMLSyntaxError:
            return data
        for entry in encryption.xpath("//*[local-name()='EncryptedData']"):
            method = entry.xpath("./*[local-name()='EncryptionMethod']/@Algorithm")
            reference = entry.xpath("./*[local-name()='CipherData']/*[local-name()='CipherReference']/@URI")
            if not method or not reference or decode_epub_href(reference[0]) != decode_epub_href(path):
                continue
            if method[0] != "http://www.idpf.org/2008/embedding":
                return data
            package = _xml(archive.read(session.opf_path))
            identifier_id = package.get("unique-identifier", "")
            identifiers = package.xpath("//*[local-name()='identifier' and @id=$id]/text()", id=identifier_id)
            if not identifiers:
                return data
            identifier = re.sub(r"[ \t\r\n]", "", identifiers[0])
            key = hashlib.sha1(identifier.encode("utf-8")).digest()
            head = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data[:1040]))
            return head + data[1040:]
    return data


class ContentSessionStore:
    def __init__(self, state_root: Path | None = None) -> None:
        self.sessions: dict[str, ContentSession] = {}
        self.state_root = state_root
        if state_root is not None:
            state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for stale in state_root.glob("*.json"):
                if time.time() - stale.stat().st_mtime > 7 * 86400:
                    stale.unlink(missing_ok=True)

    def _state_path(self, session_id: str) -> Path | None:
        if self.state_root is None:
            return None
        if len(session_id) != 32 or any(char not in "0123456789abcdef" for char in session_id):
            raise ValueError("Invalid editor session ID.")
        return self.state_root / f"{session_id}.json"

    @staticmethod
    def _pack(snapshot: SessionSnapshot) -> dict[str, object]:
        changes, spine, toc, files, removed, nav_path, ncx_path, spine_linear = snapshot
        return {
            "changes": {path: base64.b64encode(data).decode("ascii") for path, data in changes.items()},
            "spine": spine,
            "toc": toc,
            "files": files,
            "removed": sorted(removed),
            "nav_path": nav_path,
            "ncx_path": ncx_path,
            "spine_linear": spine_linear,
        }

    @staticmethod
    def _unpack(
        value: dict[str, object], base_files: list[dict[str, object]],
        nav_path: str, ncx_path: str, base_linear: dict[str, bool],
    ) -> SessionSnapshot:
        changes = value["changes"]
        if not isinstance(changes, dict):
            raise ValueError("Invalid saved editor session.")
        return (
            {str(path): base64.b64decode(str(data), validate=True) for path, data in changes.items()},
            list(value["spine"]),
            list(value["toc"]),
            copy.deepcopy(value.get("files", base_files)),
            set(value.get("removed", [])),
            str(value.get("nav_path", nav_path)),
            str(value.get("ncx_path", ncx_path)),
            dict(value.get("spine_linear", base_linear)),
        )

    def persist(self, session_id: str) -> None:
        target = self._state_path(session_id)
        if target is None:
            return
        session = self.sessions[session_id]
        payload = {
            "version": 2,
            "path": str(session.path),
            "fingerprint": session.fingerprint,
            "current": self._pack(session._snapshot()),
            "undo": [self._pack(snapshot) for snapshot in session.undo_stack],
            "redo": [self._pack(snapshot) for snapshot in session.redo_stack],
            "dirty": session.dirty,
            "checkpoints": {name: self._pack(snapshot) for name, snapshot in session.checkpoints.items()},
        }
        fd, temp_name = tempfile.mkstemp(prefix=".session-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            os.replace(temp_name, target)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def open(self, path: str) -> dict[str, object]:
        session = ContentSession.open(path)
        session_id = uuid.uuid4().hex
        self.sessions[session_id] = session
        self.persist(session_id)
        return session.info(session_id)

    def get(self, session_id: str) -> ContentSession:
        session = self.sessions.get(session_id)
        if session is None:
            state = self._state_path(session_id)
            if state is None or not state.is_file():
                raise ValueError("Editor session expired. Reopen the EPUB.")
            try:
                payload = json.loads(state.read_text(encoding="utf-8"))
                if payload["version"] not in {1, 2}:
                    raise ValueError("Unsupported editor session version.")
                session = ContentSession.open(payload["path"])
                if list(session.fingerprint) != payload["fingerprint"]:
                    raise ValueError("Source EPUB changed on disk; reopen it before editing.")
                base_files = session.files
                nav_path, ncx_path = session.nav_path, session.ncx_path
                base_linear = session.spine_linear
                (
                    session.changes, session.spine, session.toc, session.files,
                    session.removed, session.nav_path, session.ncx_path,
                    session.spine_linear,
                ) = self._unpack(payload["current"], base_files, nav_path, ncx_path, base_linear)
                session.undo_stack = [
                    self._unpack(item, base_files, nav_path, ncx_path, base_linear)
                    for item in payload["undo"]
                ]
                session.redo_stack = [
                    self._unpack(item, base_files, nav_path, ncx_path, base_linear)
                    for item in payload["redo"]
                ]
                session.dirty = session._snapshot() != session.clean_snapshot
                checkpoints = payload.get("checkpoints", {})
                if not isinstance(checkpoints, dict) or len(checkpoints) > 10 or any(
                    not isinstance(name, str) or not name.strip() or len(name) > 80
                    for name in checkpoints
                ):
                    raise ValueError("Editor checkpoints could not be restored; reopen the EPUB.")
                session.checkpoints = {
                    name: self._unpack(item, base_files, nav_path, ncx_path, base_linear)
                    for name, item in checkpoints.items()
                }
            except (KeyError, TypeError, json.JSONDecodeError, base64.binascii.Error) as exc:
                raise ValueError("Editor session could not be restored; reopen the EPUB.") from exc
            self.sessions[session_id] = session
        return session

    def close(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        state = self._state_path(session_id)
        if state is not None:
            state.unlink(missing_ok=True)
