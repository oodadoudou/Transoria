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
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

from lxml import etree
from lxml import html as lxml_html

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


def _xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=XML_PARSER)


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def _fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _decode(data: bytes) -> tuple[str, str]:
    head = data[:200].decode("ascii", errors="ignore")
    match = re.search(r"(?:encoding\s*=\s*|@charset\s+)[\"']([^\"']+)", head, re.I)
    encoding = match.group(1) if match else "utf-8"
    try:
        return data.decode(encoding), encoding
    except (LookupError, UnicodeError) as exc:
        raise ValueError(f"Unsupported text encoding: {encoding}") from exc


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
    formatted = etree.tostring(root.getroottree(), encoding=encoding, xml_declaration=True)
    return _decode(formatted)[0]


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
    changes: dict[str, bytes] = field(default_factory=dict)
    dirty: bool = False
    undo_stack: list[tuple[dict[str, bytes], list[str], list[dict[str, object]]]] = (
        field(default_factory=list)
    )
    redo_stack: list[tuple[dict[str, bytes], list[str], list[dict[str, object]]]] = (
        field(default_factory=list)
    )

    @classmethod
    def open(cls, path: str) -> ContentSession:
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != ".epub":
            raise ValueError("Select an existing EPUB file.")
        with zipfile.ZipFile(source) as archive:
            container = _xml(archive.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise ValueError("EPUB package document is missing.")
            opf_path = find_archive_entry_by_normalized_path(
                archive, rootfile.get("full-path")
            )
            if not opf_path:
                raise ValueError("EPUB package document is missing.")
            opf = _xml(archive.read(opf_path))
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
                id_to_path[item_id] = entry
                files.append(
                    {
                        "path": entry,
                        "media_type": media,
                        "editable": media in EDITABLE_TYPES,
                        "size": archive.getinfo(entry).file_size,
                    }
                )
                if "nav" in item.get("properties", "").split():
                    nav_path = entry
                if media == "application/x-dtbncx+xml":
                    ncx_path = entry
            spine = [
                id_to_path[item.get("idref", "")]
                for item in opf.findall(f".//{{{OPF}}}spine/{{{OPF}}}itemref")
                if item.get("idref", "") in id_to_path
            ]
            toc = _read_toc(archive, nav_path, ncx_path)
        return cls(
            source,
            _fingerprint(source),
            opf_path,
            files,
            spine,
            toc,
            nav_path,
            ncx_path,
        )

    def info(self, session_id: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "input_path": str(self.path),
            "files": self.files,
            "spine": self.spine,
            "toc": self.toc,
            "nav_path": self.nav_path,
            "ncx_path": self.ncx_path,
            "dirty": self.dirty,
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
        }

    def _snapshot(self) -> tuple[dict[str, bytes], list[str], list[dict[str, object]]]:
        return self.changes.copy(), self.spine.copy(), copy.deepcopy(self.toc)

    def _record(self) -> None:
        self.undo_stack.append(self._snapshot())
        self.undo_stack = self.undo_stack[-30:]
        self.redo_stack.clear()
        self.dirty = True

    def history(self, direction: str) -> None:
        source = self.undo_stack if direction == "undo" else self.redo_stack
        target = self.redo_stack if direction == "undo" else self.undo_stack
        if not source:
            raise ValueError("Nothing to undo or redo.")
        target.append(self._snapshot())
        self.changes, self.spine, self.toc = source.pop()
        self.dirty = bool(self.undo_stack)

    def _file(self, path: str, *, editable: bool = False) -> dict[str, object]:
        found = next((item for item in self.files if item["path"] == path), None)
        if found is None or (editable and not found["editable"]):
            raise ValueError("Resource is not editable or is not in the EPUB manifest.")
        return found

    def _bytes(self, path: str) -> bytes:
        if path in self.changes:
            return self.changes[path]
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

    def write(self, path: str, content: str) -> None:
        self._file(path, editable=True)
        if len(content.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("Resource exceeds the 4 MB editor limit.")
        encoding = _decode(self._bytes(path))[1]
        updated = _encode(content, encoding)
        if updated == self._bytes(path):
            return
        self._record()
        self.changes[path] = updated

    def reorder_spine(self, paths: list[str]) -> None:
        if len(paths) != len(self.spine) or set(paths) != set(self.spine):
            raise ValueError(
                "Reading order must contain every existing spine item exactly once."
            )
        if paths != self.spine:
            self._record()
            self.spine = paths.copy()

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
        for index, entry in enumerate(entries):
            label = str(entry.get("label", "")).strip()
            href = str(entry.get("href", "")).strip()
            depth = entry.get("depth", 0)
            target = href.split("#", 1)[0]
            if (
                not label
                or not target
                or target not in valid_files
                or not isinstance(depth, int)
                or depth < 0
                or depth > 8
                or (index == 0 and depth != 0)
                or (index > 0 and depth > int(normalized[-1]["depth"]) + 1)
            ):
                raise ValueError(f"Invalid table-of-contents entry at row {index + 1}.")
            normalized.append({"label": label, "href": href, "depth": depth})
        if normalized != self.toc:
            self._record()
            self.toc = normalized

    def search(
        self, query: str, paths: list[str], case_sensitive: bool = False
    ) -> list[dict[str, object]]:
        if not query:
            return []
        if len(query) > 2000:
            raise ValueError("Search text is too long.")
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(query), flags)
        results: list[dict[str, object]] = []
        for path in dict.fromkeys(paths):
            item = self._file(path, editable=True)
            if int(item["size"]) > MAX_TEXT_BYTES and path not in self.changes:
                continue
            data = self._bytes(path)
            if len(data) > MAX_TEXT_BYTES:
                continue
            content = _editor_text(data, str(item["media_type"]))
            for match in pattern.finditer(content):
                results.append(
                    {
                        "path": path,
                        "start": match.start(),
                        "end": match.end(),
                        "excerpt": content[
                            max(0, match.start() - 45) : min(
                                len(content), match.end() + 65
                            )
                        ].replace("\n", " "),
                    }
                )
                if len(results) >= 5000:
                    return results
        return results

    def replace(
        self,
        query: str,
        replacement: str,
        paths: list[str],
        case_sensitive: bool = False,
        expected_count: int | None = None,
    ) -> dict[str, object]:
        if not query:
            raise ValueError("Search text is required.")
        pattern = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
        pending: dict[str, bytes] = {}
        total = 0
        for path in dict.fromkeys(paths):
            item = self._file(path, editable=True)
            if int(item["size"]) > MAX_TEXT_BYTES and path not in self.changes:
                raise ValueError(f"Resource exceeds the editor limit: {path}")
            data = self._bytes(path)
            if len(data) > MAX_TEXT_BYTES:
                raise ValueError(f"Resource exceeds the editor limit: {path}")
            content = _editor_text(data, str(item["media_type"]))
            encoding = _decode(data)[1]
            updated, count = pattern.subn(lambda _: replacement, content)
            if count:
                pending[path] = _encode(updated, encoding)
                total += count
        if expected_count is not None and total != expected_count:
            raise ValueError("Search results changed; search again before replacing.")
        if pending:
            self._record()
            self.changes.update(pending)
        return {"replacements": total, "files_changed": len(pending)}

    def replace_match(
        self,
        path: str,
        start: int,
        end: int,
        query: str,
        replacement: str,
        case_sensitive: bool = False,
    ) -> None:
        item = self._file(path, editable=True)
        data = self._bytes(path)
        content = _editor_text(data, str(item["media_type"]))
        encoding = _decode(data)[1]
        if not 0 <= start < end <= len(content):
            raise ValueError("Search result is no longer current; search again.")
        found = content[start:end]
        if (found if case_sensitive else found.casefold()) != (
            query if case_sensitive else query.casefold()
        ):
            raise ValueError("Search result is no longer current; search again.")
        self._record()
        self.changes[path] = _encode(
            content[:start] + replacement + content[end:], encoding
        )

    def preview(
        self, path: str, draft_path: str = "", draft_content: str | None = None
    ) -> str:
        item = self._file(path)
        if item["media_type"] not in {"application/xhtml+xml", "text/html"}:
            raise ValueError("Preview is available for XHTML/HTML resources only.")
        drafts: dict[str, bytes] = {}
        if draft_path and draft_content is not None:
            self._file(draft_path, editable=True)
            if len(draft_content.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ValueError("Resource exceeds the 4 MB editor limit.")
            drafts[draft_path] = _encode(
                draft_content, _decode(self._bytes(draft_path))[1]
            )
        markup_bytes = drafts.get(path, self._bytes(path))
        if item["media_type"] == "text/html":
            root = lxml_html.fromstring(markup_bytes)
        else:
            try:
                root = _xml(markup_bytes)
            except etree.XMLSyntaxError:
                root = lxml_html.fromstring(markup_bytes)
        for node in list(root.iter()):
            local = (
                etree.QName(node).localname.lower() if isinstance(node.tag, str) else ""
            )
            if local in {"script", "iframe", "object", "embed", "form", "base"}:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                continue
            if local == "style" and node.text:
                node.text = _inline_css(node.text, path, self, drafts)
            if node.get("style"):
                node.set("style", _inline_css(node.get("style", ""), path, self, drafts))
            for key in list(node.attrib):
                if etree.QName(key).localname.lower().startswith("on"):
                    del node.attrib[key]
            for attr in ("src", "href", "poster"):
                value = node.get(attr)
                if not value or value.startswith(("#", "data:")):
                    continue
                if urlsplit(value).scheme or value.startswith("//"):
                    node.attrib.pop(attr, None)
                    continue
                target = resolve_epub_href(posixpath.dirname(path), value)
                known = next(
                    (file for file in self.files if file["path"] == target), None
                )
                if (
                    local == "link"
                    and attr == "href"
                    and known
                    and known["media_type"] == "text/css"
                ):
                    css = _decode(drafts.get(target, self._bytes(target)))[0]
                    style = etree.Element(f"{{{XHTML}}}style" if etree.QName(root).namespace == XHTML else "style")
                    style.text = _inline_css(css, target, self, drafts)
                    node.getparent().replace(node, style)
                    break
                if (
                    attr in {"src", "poster"}
                    and known
                    and str(known["media_type"]).startswith("image/")
                ):
                    data = self._bytes(target)
                    node.set(
                        attr,
                        f"data:{known['media_type']};base64,{base64.b64encode(data).decode('ascii')}",
                    )
                elif attr == "href":
                    node.set(attr, "#")
                else:
                    node.attrib.pop(attr, None)
        markup = etree.tostring(root, encoding="unicode", method="html")
        if len(markup.encode("utf-8")) > MAX_PREVIEW_BYTES:
            raise ValueError("Preview exceeds the 48 MB limit.")
        csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:"
        fit_media = (
            "<style>img,svg,video{max-width:100%!important;"
            "max-height:calc(100vh - 24px)!important;"
            "width:auto!important;height:auto!important;object-fit:contain!important}"
            "html,body{max-width:100%;box-sizing:border-box;overflow-wrap:anywhere}</style>"
        )
        return (
            f'<meta http-equiv="Content-Security-Policy" content="{html.escape(csp)}">'
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
            ref_by_path = {}
            for itemref in itemrefs:
                item = manifest.get(itemref.get("idref", ""))
                if item is not None:
                    resolved = resolve_epub_href(
                        posixpath.dirname(self.opf_path), item.get("href", "")
                    )
                    entry = find_archive_entry_by_normalized_path(source, resolved)
                    if entry:
                        ref_by_path[entry] = itemref
            if set(self.spine) != set(ref_by_path):
                raise ValueError("Reading order no longer matches the package.")
            if list(ref_by_path) != self.spine:
                if len(itemrefs) != len(ref_by_path):
                    raise ValueError(
                        "Reading order contains unresolved items; cannot reorder safely."
                    )
                for itemref in itemrefs:
                    spine_node.remove(itemref)
                for path in self.spine:
                    spine_node.append(ref_by_path[path])
                pending[self.opf_path] = _serialize(opf)
            if self.toc != _read_toc(source, self.nav_path, self.ncx_path):
                if self.nav_path:
                    pending[self.nav_path] = _write_nav(
                        pending.get(self.nav_path, source.read(self.nav_path)),
                        self.nav_path,
                        self.toc,
                    )
                if self.ncx_path:
                    pending[self.ncx_path] = _write_ncx(
                        pending.get(self.ncx_path, source.read(self.ncx_path)),
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
            fd, temp_name = tempfile.mkstemp(
                prefix=".epub-content-", suffix=".epub", dir=output.parent
            )
            os.close(fd)
            temp = Path(temp_name)
            try:
                with zipfile.ZipFile(temp, "w") as target:
                    for info in source.infolist():
                        data = (
                            pending.pop(info.filename)
                            if info.filename in pending
                            else source.read(info.filename)
                        )
                        target.writestr(info, data)
                if pending:
                    raise ValueError(
                        f"Edited resource is missing from archive: {next(iter(pending))}"
                    )
                with zipfile.ZipFile(temp) as check_archive:
                    bad = check_archive.testzip()
                    if bad:
                        raise ValueError(f"EPUB archive corruption: {bad}")
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
        self.path = output
        self.fingerprint = _fingerprint(output)
        self.changes.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        return {"output_path": str(output), "structure_check": after_check}

    def validate(self) -> dict[str, object]:
        candidate = copy.copy(self)
        candidate.changes = self.changes.copy()
        candidate.spine = self.spine.copy()
        candidate.toc = copy.deepcopy(self.toc)
        with tempfile.TemporaryDirectory(prefix="transoria-epub-check-") as folder:
            result = candidate.save(str(Path(folder) / "checked.epub"), overwrite=False)
        return {"structure_check": result["structure_check"]}


def _read_toc(
    archive: zipfile.ZipFile, nav_path: str, ncx_path: str
) -> list[dict[str, object]]:
    if nav_path:
        root = _xml(archive.read(nav_path))
        nav = next(
            (
                n
                for n in root.iter()
                if isinstance(n.tag, str)
                and etree.QName(n).localname == "nav"
                and (
                    n.get("{http://www.idpf.org/2007/ops}type") == "toc"
                    or n.get("role") == "doc-toc"
                )
            ),
            None,
        )
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
        root = _xml(archive.read(ncx_path))
        nav_map = root.find(f".//{{{NCX}}}navMap")
        if nav_map is not None:
            result: list[dict[str, object]] = []

            def walk(parent: etree._Element, depth: int) -> None:
                for point in parent.findall(f"{{{NCX}}}navPoint"):
                    label = "".join(
                        point.xpath(
                            "./*[local-name()='navLabel']/*[local-name()='text']/text()"
                        )
                    )
                    content = point.find(f"{{{NCX}}}content")
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
    return []


def _toc_from_nav(
    ol: etree._Element, nav_path: str, archive: zipfile.ZipFile
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []

    def walk(parent: etree._Element, depth: int) -> None:
        for li in parent:
            if not isinstance(li.tag, str) or etree.QName(li).localname != "li":
                continue
            link = next(
                (
                    n
                    for n in li
                    if isinstance(n.tag, str)
                    and etree.QName(n).localname in {"a", "span"}
                ),
                None,
            )
            if link is not None and link.get("href"):
                result.append(
                    {
                        "label": " ".join("".join(link.itertext()).split()),
                        "href": _absolute_href(nav_path, link.get("href", ""), archive),
                        "depth": depth,
                    }
                )
            nested = next(
                (
                    n
                    for n in li
                    if isinstance(n.tag, str) and etree.QName(n).localname == "ol"
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
            and etree.QName(n).localname == "nav"
            and (
                n.get("{http://www.idpf.org/2007/ops}type") == "toc"
                or n.get("role") == "doc-toc"
            )
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
        li = etree.SubElement(levels[-1], f"{{{XHTML}}}li")
        link = etree.SubElement(
            li, f"{{{XHTML}}}a", href=_relative_href(path, str(entry["href"]))
        )
        link.text = str(entry["label"])
    return _serialize(root)


def _write_ncx(data: bytes, path: str, entries: list[dict[str, object]]) -> bytes:
    root = _xml(data)
    nav_map = root.find(f".//{{{NCX}}}navMap")
    if nav_map is None:
        raise ValueError("NCX navigation map is missing.")
    for point in nav_map.findall(f"{{{NCX}}}navPoint"):
        nav_map.remove(point)
    levels = [nav_map]
    for index, entry in enumerate(entries):
        depth = int(entry["depth"])
        while len(levels) > depth + 1:
            levels.pop()
        if depth + 1 > len(levels):
            levels.append(levels[-1][-1])
        point = etree.SubElement(
            levels[-1],
            f"{{{NCX}}}navPoint",
            id=f"transoria-nav-{index+1}",
            playOrder=str(index + 1),
        )
        label = etree.SubElement(point, f"{{{NCX}}}navLabel")
        etree.SubElement(label, f"{{{NCX}}}text").text = str(entry["label"])
        etree.SubElement(
            point, f"{{{NCX}}}content", src=_relative_href(path, str(entry["href"]))
        )
    return _serialize(root)


def _inline_css(
    css: str, base: str, session: ContentSession,
    drafts: dict[str, bytes] | None = None, visited: frozenset[str] = frozenset(),
) -> str:
    drafts = drafts or {}
    if base in visited:
        return ""
    visited = visited | {base}

    def import_css(match: re.Match[str]) -> str:
        href = match.group(2) or match.group(4)
        if not href or urlsplit(href).scheme or href.startswith("//"):
            return ""
        path = resolve_epub_href(posixpath.dirname(base), href)
        item = next((file for file in session.files if file["path"] == path), None)
        if not item or item["media_type"] != "text/css":
            return ""
        if len(session._bytes(path)) > MAX_TEXT_BYTES:
            return ""
        imported = _inline_css(_decode(drafts.get(path, session._bytes(path)))[0], path, session, drafts, visited)
        media = match.group(5).strip()
        return f"@media {media} {{{imported}}}" if media and not media.startswith(("layer", "supports")) else imported

    css = re.sub(
        r"@import\s+(?:url\(\s*(['\"]?)(.*?)\1\s*\)|(['\"])(.*?)\3)\s*([^;]*);",
        import_css, css, flags=re.I,
    )

    def replace(match: re.Match[str]) -> str:
        raw = match.group(2).strip()
        if (
            not raw
            or raw.startswith(("data:", "#"))
            or urlsplit(raw).scheme
            or raw.startswith("//")
        ):
            return "url()" if urlsplit(raw).scheme != "data" and not raw.startswith("#") else match.group(0)
        path = resolve_epub_href(posixpath.dirname(base), raw)
        item = next((file for file in session.files if file["path"] == path), None)
        if not item:
            return "url()"
        data = drafts.get(path, session._bytes(path))
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
        return f"url(data:{media};base64,{base64.b64encode(data).decode('ascii')})"

    return re.sub(r"url\(\s*(['\"]?)(.*?)\1\s*\)", replace, css, flags=re.I)


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
    def _pack(snapshot: tuple[dict[str, bytes], list[str], list[dict[str, object]]]) -> dict[str, object]:
        changes, spine, toc = snapshot
        return {
            "changes": {path: base64.b64encode(data).decode("ascii") for path, data in changes.items()},
            "spine": spine,
            "toc": toc,
        }

    @staticmethod
    def _unpack(value: dict[str, object]) -> tuple[dict[str, bytes], list[str], list[dict[str, object]]]:
        changes = value["changes"]
        if not isinstance(changes, dict):
            raise ValueError("Invalid saved editor session.")
        return (
            {str(path): base64.b64decode(str(data), validate=True) for path, data in changes.items()},
            list(value["spine"]),
            list(value["toc"]),
        )

    def persist(self, session_id: str) -> None:
        target = self._state_path(session_id)
        if target is None:
            return
        session = self.sessions[session_id]
        payload = {
            "version": 1,
            "path": str(session.path),
            "fingerprint": session.fingerprint,
            "current": self._pack(session._snapshot()),
            "undo": [self._pack(snapshot) for snapshot in session.undo_stack],
            "redo": [self._pack(snapshot) for snapshot in session.redo_stack],
            "dirty": session.dirty,
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
                if payload["version"] != 1:
                    raise ValueError("Unsupported editor session version.")
                session = ContentSession.open(payload["path"])
                if list(session.fingerprint) != payload["fingerprint"]:
                    raise ValueError("Source EPUB changed on disk; reopen it before editing.")
                session.changes, session.spine, session.toc = self._unpack(payload["current"])
                session.undo_stack = [self._unpack(item) for item in payload["undo"]]
                session.redo_stack = [self._unpack(item) for item in payload["redo"]]
                session.dirty = bool(payload["dirty"])
            except (KeyError, TypeError, json.JSONDecodeError, base64.binascii.Error) as exc:
                raise ValueError("Editor session could not be restored; reopen the EPUB.") from exc
            self.sessions[session_id] = session
        return session

    def close(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        state = self._state_path(session_id)
        if state is not None:
            state.unlink(missing_ok=True)
