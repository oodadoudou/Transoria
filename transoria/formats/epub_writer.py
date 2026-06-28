from __future__ import annotations

import copy
from pathlib import Path
import zipfile

from lxml import etree

from transoria.domain import Language, normalize_target_script, translated_filename
from transoria.formats.epub_parser import (
    EpubDocument,
    EpubTextKind,
    build_elem_by_path,
    find_by_path,
    local_name,
    normalize_slot_text,
    parse_ncx_xml,
    parse_xhtml_or_html,
    sha1_with_null_separator,
)
from transoria.formats.epub_paths import decode_epub_href
from transoria.formats.text import BILINGUAL_OUTPUT_FOLDER_EN


def write_translated_epub(
    document: EpubDocument,
    translations: dict[int, str],
    output_dir: Path,
    *,
    target_language: Language,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / translated_filename(document.path, target_language)
    normalized = _normalize_translations(translations, target_language)
    _write_epub(document, normalized, output_path, bilingual=False)
    return output_path


def write_bilingual_epub(
    document: EpubDocument,
    translations: dict[int, str],
    output_dir: Path,
    *,
    source_language: Language,
    target_language: Language,
    subfolder: str = BILINGUAL_OUTPUT_FOLDER_EN,
    dedup_when_same: bool = True,
) -> Path:
    bilingual_dir = output_dir / subfolder
    bilingual_dir.mkdir(parents=True, exist_ok=True)
    output_path = bilingual_dir / translated_filename(
        document.path,
        target_language,
        source_language=source_language,
        bilingual=True,
    )
    normalized = _normalize_translations(translations, target_language)
    _write_epub(
        document,
        normalized,
        output_path,
        bilingual=True,
        dedup_when_same=dedup_when_same,
    )
    return output_path


def write_epub_to_path(
    document: EpubDocument,
    translations: dict[int, str],
    output_path: Path,
    *,
    bilingual: bool = False,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_epub(document, translations, output_path, bilingual=bilingual)
    return output_path


def _normalize_translations(
    translations: dict[int, str],
    target_language: Language,
) -> dict[int, str]:
    return {
        index: normalize_target_script(text, target_language)
        for index, text in translations.items()
    }


def _write_epub(
    document: EpubDocument,
    translations: dict[int, str],
    output_path: Path,
    *,
    bilingual: bool,
    dedup_when_same: bool = True,
) -> None:
    segments_by_doc = _segments_by_doc(document, translations)

    if document.archive_bytes is not None:
        import io

        source_handle = zipfile.ZipFile(io.BytesIO(document.archive_bytes), "r")
    else:
        source_handle = zipfile.ZipFile(document.path, "r")
    with source_handle as source_archive:
        with zipfile.ZipFile(output_path, "w") as output_archive:
            for info in source_archive.infolist():
                raw = source_archive.read(info.filename)
                doc_key = _segments_doc_key(info.filename, segments_by_doc)
                if doc_key:
                    raw = _apply_doc_segments(
                        raw,
                        doc_key,
                        segments_by_doc[doc_key],
                        translations,
                        bilingual,
                        dedup_when_same=dedup_when_same,
                    )

                output_archive.writestr(_clone_zip_info(info), raw)


def _segments_by_doc(document: EpubDocument, translations: dict[int, str]):
    result = {}
    for segment in document.segments:
        if segment.index not in translations:
            continue
        result.setdefault(segment.doc_path, []).append(segment)

    for segments in result.values():
        segments.sort(key=lambda segment: segment.row)
    return result


def _segments_doc_key(filename: str, segments_by_doc) -> str:
    if filename in segments_by_doc:
        return filename
    normalized = decode_epub_href(filename)
    matches = [
        doc_path
        for doc_path in segments_by_doc
        if decode_epub_href(doc_path) == normalized
    ]
    return matches[0] if len(matches) == 1 else ""


def _apply_doc_segments(
    raw: bytes,
    doc_path: str,
    segments,
    translations: dict[int, str],
    bilingual: bool,
    *,
    dedup_when_same: bool = True,
) -> bytes:
    root = _parse_doc(raw, doc_path)
    elem_by_path = build_elem_by_path(root)
    block_refs: list[tuple[etree._Element, etree._Element]] = []
    inserted_block_paths: set[str] = set()
    allow_bilingual = bilingual and not _is_nav_or_metadata_doc(doc_path, root, segments)

    for segment in segments:
        translation = translations[segment.index]
        translated_lines = translation.split("\n")

        resolved: list[tuple[str, etree._Element]] = []
        current_texts: list[str] = []
        for part in segment.parts:
            elem = _resolve_elem(root, elem_by_path, part.path)
            if elem is None:
                break
            if part.slot == "text":
                current_texts.append(normalize_slot_text(elem.text or ""))
            elif part.slot == "tail":
                current_texts.append(normalize_slot_text(elem.tail or ""))
            else:
                break
            resolved.append((part.slot, elem))
        else:
            if sha1_with_null_separator(current_texts) != segment.source_digest:
                continue

            should_insert_bilingual_block = allow_bilingual and (
                not dedup_when_same or segment.text != translation
            )
            if should_insert_bilingual_block and segment.block_path not in inserted_block_paths:
                block = _resolve_elem(root, elem_by_path, segment.block_path)
                if block is not None:
                    block_refs.append((block, copy.deepcopy(block)))
                    inserted_block_paths.add(segment.block_path)

            if len(translated_lines) == len(resolved):
                replacements = translated_lines
            else:
                replacements = [translation] + [""] * (len(resolved) - 1)

            for (slot, elem), translated_text in zip(resolved, replacements, strict=True):
                if slot == "text":
                    elem.text = translated_text
                else:
                    elem.tail = translated_text
            if _segment_uses_ruby(segment):
                block = _resolve_elem(root, elem_by_path, segment.block_path)
                if block is not None:
                    _remove_ruby_annotations(block)

    if allow_bilingual:
        for block, clone in reversed(block_refs):
            parent = block.getparent()
            if parent is None:
                continue
            _mark_bilingual_clone(clone)
            parent.insert(parent.index(block), clone)
            clone.tail = clone.tail or "\n"

    return _serialize_doc(root, doc_path)


def _parse_doc(raw: bytes, doc_path: str) -> etree._Element:
    lower = doc_path.lower()
    if lower.endswith(".opf"):
        return etree.fromstring(raw, parser=etree.XMLParser(recover=True, resolve_entities=True, no_network=True))
    if lower.endswith(".ncx"):
        return parse_ncx_xml(raw)
    return parse_xhtml_or_html(raw)


def _resolve_elem(
    root: etree._Element,
    elem_by_path: dict[str, etree._Element],
    path: str,
) -> etree._Element | None:
    elem = elem_by_path.get(path)
    if elem is not None:
        return elem
    return find_by_path(root, path)


def _is_nav_or_metadata_doc(doc_path: str, root: etree._Element, segments) -> bool:
    lower = doc_path.lower()
    if lower.endswith(".opf") or lower.endswith(".ncx"):
        return True
    if any(segment.kind in {EpubTextKind.NAV, EpubTextKind.NCX} for segment in segments):
        return True
    return _is_nav_page(root)


def _segment_uses_ruby(segment) -> bool:
    return any("/ruby[" in part.path or "/rt[" in part.path or "/rp[" in part.path for part in segment.parts)


def _remove_ruby_annotations(block: etree._Element) -> None:
    for elem in list(block.xpath(".//*[local-name()='rt' or local-name()='rp']")):
        parent = elem.getparent()
        if parent is not None:
            parent.remove(elem)


def _is_nav_page(root: etree._Element) -> bool:
    for nav in root.xpath(".//*[local-name()='nav']"):
        for key, value in nav.attrib.items():
            key_text = str(key)
            if key_text == "epub:type" or key_text.endswith(":type") or key_text.endswith("}type"):
                if value in {"toc", "landmarks"}:
                    return True
    return False


def _mark_bilingual_clone(clone: etree._Element) -> None:
    style = clone.get("style", "").rstrip(";")
    clone.set("style", f"{style + ';' if style else ''}opacity:0.50;")


def _serialize_doc(root: etree._Element, doc_path: str) -> bytes:
    tag = str(root.tag)
    if tag.lower() == "html" and not tag.startswith("{"):
        return etree.tostring(root, encoding="utf-8", method="html")
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def _clone_zip_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
    clone.comment = info.comment
    clone.extra = info.extra
    clone.internal_attr = info.internal_attr
    clone.external_attr = info.external_attr
    clone.create_system = info.create_system
    clone.compress_type = zipfile.ZIP_STORED if info.filename == "mimetype" else info.compress_type
    return clone
