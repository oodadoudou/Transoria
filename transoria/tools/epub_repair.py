from __future__ import annotations

import copy
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Mapping
import zipfile

from lxml import etree

from transoria.formats.epub_parser import (
    HTML_DOCUMENT_SUFFIXES,
    fix_ncx_bare_ampersands,
    local_name,
    normalize_epub_xml_entities,
    normalize_html_named_entities_for_xml,
    parse_epub_xml,
    parse_ncx_xml,
    parse_xhtml_or_html,
    parsed_root_preserves_body,
    repair_redundant_void_end_tags,
    trim_to_html_document_start,
)
from transoria.tools.epub_structure import (
    compare_epub_structure_checks,
    inspect_epub_structure,
)


_EPUB_SUFFIX = ".epub"
_XML_SUFFIXES = (".opf", ".ncx", ".xml")
_MIMETYPE = b"application/epub+zip"
_XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_VOID_CONTAINER_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(frozen=True)
class EpubRepairResult:
    input_path: Path
    output_path: Path
    documents_scanned: int
    documents_repaired: int
    html_files_scanned: int
    html_files_repaired: int
    xml_files_scanned: int
    xml_files_repaired: int
    void_containers_repaired: int
    document_wrappers_added: int
    outcome: str = "success"
    structure_check: Mapping[str, object] | None = None
    structure_comparison: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "documents_scanned": self.documents_scanned,
            "documents_repaired": self.documents_repaired,
            "html_files_scanned": self.html_files_scanned,
            "html_files_repaired": self.html_files_repaired,
            "xml_files_scanned": self.xml_files_scanned,
            "xml_files_repaired": self.xml_files_repaired,
            "void_containers_repaired": self.void_containers_repaired,
            "document_wrappers_added": self.document_wrappers_added,
            "outcome": self.outcome,
        }
        if self.structure_check is not None:
            payload["structure_check"] = dict(self.structure_check)
        if self.structure_comparison is not None:
            payload["structure_comparison"] = dict(self.structure_comparison)
        return payload


@dataclass(frozen=True)
class EpubRepairPreview:
    input_path: Path
    output_path: Path
    documents_scanned: int
    documents_to_repair: int
    html_files_scanned: int
    html_files_to_repair: int
    xml_files_scanned: int
    xml_files_to_repair: int
    void_containers_to_repair: int
    document_wrappers_to_add: int
    structure_check: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "documents_scanned": self.documents_scanned,
            "documents_to_repair": self.documents_to_repair,
            "html_files_scanned": self.html_files_scanned,
            "html_files_to_repair": self.html_files_to_repair,
            "xml_files_scanned": self.xml_files_scanned,
            "xml_files_to_repair": self.xml_files_to_repair,
            "void_containers_to_repair": self.void_containers_to_repair,
            "document_wrappers_to_add": self.document_wrappers_to_add,
            "would_change": self.documents_to_repair > 0,
            "structure_check": dict(self.structure_check),
        }


def preview_epub_repair(
    input_path: str | Path,
    output_path: str | Path | None = None,
) -> EpubRepairPreview:
    source = _validate_epub_path(input_path)
    requested_output = _resolve_output_path(source, output_path)
    html_scanned = 0
    html_repaired = 0
    xml_scanned = 0
    xml_repaired = 0
    voids = 0
    wrappers = 0
    with zipfile.ZipFile(source, "r") as archive:
        for info in archive.infolist():
            filename = info.filename.lower()
            raw = archive.read(info.filename)
            if filename.endswith(HTML_DOCUMENT_SUFFIXES):
                html_scanned += 1
                repaired = _repair_html_document(raw)
                if repaired.changed:
                    html_repaired += 1
                    voids += repaired.void_containers
                    wrappers += repaired.document_wrappers
            elif filename.endswith(_XML_SUFFIXES):
                xml_scanned += 1
                repaired = _repair_xml_document(raw, is_ncx=filename.endswith(".ncx"))
                if repaired.changed:
                    xml_repaired += 1
    return EpubRepairPreview(
        input_path=source,
        output_path=requested_output,
        documents_scanned=html_scanned + xml_scanned,
        documents_to_repair=html_repaired + xml_repaired,
        html_files_scanned=html_scanned,
        html_files_to_repair=html_repaired,
        xml_files_scanned=xml_scanned,
        xml_files_to_repair=xml_repaired,
        void_containers_to_repair=voids,
        document_wrappers_to_add=wrappers,
        structure_check=inspect_epub_structure(source),
    )


def repair_epub_file(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> EpubRepairResult:
    source = _validate_epub_path(input_path)
    requested_output = _resolve_output_path(source, output_path)
    same_path = source.resolve() == requested_output.resolve()
    if same_path and not overwrite:
        raise ValueError("Output path matches input EPUB; overwrite is required.")

    target_output = requested_output if overwrite else _unique_output(requested_output)
    temp_output = (
        _temporary_epub_path(target_output)
        if same_path or target_output.exists()
        else target_output
    )

    html_files_scanned = 0
    html_files_repaired = 0
    xml_files_scanned = 0
    xml_files_repaired = 0
    void_containers_repaired = 0
    document_wrappers_added = 0
    source_check = inspect_epub_structure(source)
    try:
        temp_output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as src:
            with zipfile.ZipFile(temp_output, "w") as dst:
                for info in src.infolist():
                    raw = src.read(info.filename)
                    next_raw = raw
                    filename = info.filename.lower()
                    if filename.endswith(HTML_DOCUMENT_SUFFIXES):
                        html_files_scanned += 1
                        repaired = _repair_html_document(raw)
                        if repaired.changed:
                            next_raw = repaired.raw
                            html_files_repaired += 1
                            void_containers_repaired += repaired.void_containers
                            document_wrappers_added += repaired.document_wrappers
                    elif filename.endswith(_XML_SUFFIXES):
                        xml_files_scanned += 1
                        repaired = _repair_xml_document(raw, is_ncx=filename.endswith(".ncx"))
                        if repaired.changed:
                            next_raw = repaired.raw
                            xml_files_repaired += 1
                    dst.writestr(_clone_zip_info(info), next_raw)
        _validate_epub_archive(temp_output)
        structure_check = inspect_epub_structure(temp_output)
        comparison = compare_epub_structure_checks(
            source_check,
            structure_check,
            preserve_counts=("spine", "html", "nav", "ncx", "images", "fonts", "css"),
        )
        if comparison["status"] == "failed":
            raise ValueError("repaired EPUB failed structure validation")
        if temp_output != target_output:
            os.replace(temp_output, target_output)
    except Exception:
        try:
            temp_output.unlink()
        except FileNotFoundError:
            pass
        raise

    return EpubRepairResult(
        input_path=source,
        output_path=target_output,
        documents_scanned=html_files_scanned + xml_files_scanned,
        documents_repaired=html_files_repaired + xml_files_repaired,
        html_files_scanned=html_files_scanned,
        html_files_repaired=html_files_repaired,
        xml_files_scanned=xml_files_scanned,
        xml_files_repaired=xml_files_repaired,
        void_containers_repaired=void_containers_repaired,
        document_wrappers_added=document_wrappers_added,
        outcome=(
            "success_with_warnings"
            if comparison["status"] == "warning"
            else comparison["status"]
        ),
        structure_check=structure_check,
        structure_comparison=comparison,
    )


@dataclass(frozen=True)
class _RepairHtmlResult:
    raw: bytes
    changed: bool
    void_containers: int
    document_wrappers: int


def _repair_html_document(raw: bytes) -> _RepairHtmlResult:
    needs_reserialize = _html_needs_reserialize(raw)
    root = parse_xhtml_or_html(raw)
    root, wrappers = _normalize_html_document_root(root)
    fixed = _repair_void_containers(root)
    if fixed <= 0 and wrappers <= 0 and not needs_reserialize:
        return _RepairHtmlResult(
            raw=raw,
            changed=False,
            void_containers=0,
            document_wrappers=0,
        )
    return _RepairHtmlResult(
        raw=_serialize_xml(root),
        changed=True,
        void_containers=fixed,
        document_wrappers=wrappers,
    )


@dataclass(frozen=True)
class _RepairXmlResult:
    raw: bytes
    changed: bool


def _repair_xml_document(raw: bytes, *, is_ncx: bool) -> _RepairXmlResult:
    fixed = fix_ncx_bare_ampersands(raw) if is_ncx else normalize_epub_xml_entities(raw)
    needs_reserialize = fixed != raw or not _strict_xml_ok(raw)
    if not needs_reserialize:
        return _RepairXmlResult(raw=raw, changed=False)

    root = parse_ncx_xml(raw) if is_ncx else parse_epub_xml(raw)
    return _RepairXmlResult(raw=_serialize_xml(root), changed=True)


def _html_needs_reserialize(raw: bytes) -> bool:
    trimmed = trim_to_html_document_start(raw)
    repaired_voids = repair_redundant_void_end_tags(trimmed)
    entity_fixed = normalize_html_named_entities_for_xml(repaired_voids)
    if entity_fixed != raw:
        return True
    try:
        root = etree.fromstring(
            raw,
            parser=etree.XMLParser(
                recover=False,
                resolve_entities=True,
                no_network=True,
            ),
        )
    except Exception:
        return True
    return not parsed_root_preserves_body(raw, root)


def _strict_xml_ok(raw: bytes) -> bool:
    try:
        etree.fromstring(
            raw,
            parser=etree.XMLParser(
                recover=False,
                resolve_entities=True,
                no_network=True,
            ),
        )
        return True
    except Exception:
        return False


def _normalize_html_document_root(root: etree._Element) -> tuple[etree._Element, int]:
    name = local_name(root.tag)
    if name == "html":
        if _find_first_child(root, "body") is not None:
            return root, 0
        body = etree.Element("body")
        moved = [
            child
            for child in list(root)
            if local_name(child.tag) not in {"head", "metadata"}
        ]
        if root.text:
            body.text = root.text
            root.text = None
        for child in moved:
            root.remove(child)
            body.append(child)
        root.append(body)
        return root, 1

    html = etree.Element("html", nsmap={None: _XHTML_NAMESPACE})
    body = etree.SubElement(html, "body")
    body.append(root)
    return html, 1


def _find_first_child(root: etree._Element, name: str) -> etree._Element | None:
    for child in root.iter():
        if not isinstance(child.tag, str):
            continue
        if local_name(child.tag) == name:
            return child
    return None


def _repair_void_containers(root: etree._Element) -> int:
    repaired = 0
    for elem in reversed(list(root.iter())):
        if not isinstance(elem.tag, str):
            continue
        if local_name(elem.tag) not in _VOID_CONTAINER_TAGS:
            continue
        if not elem.text and len(elem) == 0:
            continue
        parent = elem.getparent()
        if parent is None:
            continue

        inner_text = elem.text or ""
        original_tail = elem.tail or ""
        children = list(elem)
        elem.text = None
        elem.tail = inner_text or None

        insert_at = parent.index(elem) + 1
        last_inserted: etree._Element = elem
        for child in children:
            elem.remove(child)
            parent.insert(insert_at, child)
            insert_at += 1
            last_inserted = child

        if original_tail:
            last_inserted.tail = (last_inserted.tail or "") + original_tail
        repaired += 1
    return repaired


def _serialize_xml(root: etree._Element) -> bytes:
    return etree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        method="xml",
    )


def _validate_epub_path(path: str | Path) -> Path:
    epub_path = Path(path).expanduser().resolve()
    if not epub_path.is_file():
        raise FileNotFoundError(f"EPUB not found: {epub_path}")
    if epub_path.suffix.lower() != _EPUB_SUFFIX:
        raise ValueError("Input path must be an .epub file.")
    return epub_path


def _resolve_output_path(source: Path, output_path: str | Path | None) -> Path:
    if output_path is None or not str(output_path).strip():
        return source.with_name(f"{source.stem}-repaired{source.suffix}")
    resolved = Path(output_path).expanduser().resolve()
    if resolved.suffix.lower() != _EPUB_SUFFIX:
        raise ValueError("Output path must end with .epub.")
    return resolved


def _temporary_epub_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}.transoria-repair.",
        suffix=".epub",
        dir=target.parent,
        delete=False,
    )
    handle.close()
    return Path(handle.name)


def _unique_output(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot find unique output path for {path}")


def _clone_zip_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    clone = copy.copy(info)
    if clone.filename == "mimetype":
        clone.compress_type = zipfile.ZIP_STORED
    return clone


def _validate_epub_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if not names:
            raise ValueError("output EPUB is empty")
        if names[0] != "mimetype":
            raise ValueError("output EPUB mimetype must be the first entry")
        if archive.read("mimetype").strip() != _MIMETYPE:
            raise ValueError("output EPUB mimetype is invalid")


__all__ = [
    "EpubRepairPreview",
    "EpubRepairResult",
    "preview_epub_repair",
    "repair_epub_file",
]
