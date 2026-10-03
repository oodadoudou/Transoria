from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path

from lxml import etree
import regex

from transoria.domain import Language, normalize_target_script, translated_filename
from transoria.formats.epub_parser import (
    EpubDocument,
    EpubTextKind,
    build_elem_by_path,
    find_by_path,
    local_name,
    normalize_slot_text,
    parse_ncx_xml,
    parse_package,
    parse_xhtml_or_html,
    read_archive_entry,
    sha1_with_null_separator,
)
from transoria.formats.epub_paths import decode_epub_href
from transoria.formats.text import BILINGUAL_OUTPUT_FOLDER_EN


_LOGGER = logging.getLogger(__name__)
_INITIAL_PATTERN = regex.compile(r'^[\p{Pi}\p{Ps}\x22\x27]*\X$', regex.VERSION1)
_REPORT_KIND = "transoria.epub_format_warnings"


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
    format_warnings = _write_epub(document, normalized, output_path, bilingual=False)
    _write_format_report(output_path, format_warnings)
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
    format_warnings = _write_epub(
        document,
        normalized,
        output_path,
        bilingual=True,
        dedup_when_same=dedup_when_same,
    )
    _write_format_report(output_path, format_warnings)
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


def write_epub_slots_to_path(
    document: EpubDocument,
    replacements: dict[int, tuple[str, ...]],
    output_path: Path,
) -> Path:
    segments = {segment.index: segment for segment in document.segments}
    for index, texts in replacements.items():
        if index not in segments or len(texts) != len(segments[index].parts):
            raise ValueError("EPUB slot replacement does not match the source segment")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_epub(
        document, {index: "\n".join(texts) for index, texts in replacements.items()},
        output_path, bilingual=False, slot_replacements=replacements,
    )
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
    slot_replacements: dict[int, tuple[str, ...]] | None = None,
) -> list[dict[str, object]]:
    segments_by_doc = _segments_by_doc(document, translations)
    format_warnings: list[dict[str, object]] = []

    if document.archive_bytes is not None:
        import io

        source_handle = zipfile.ZipFile(io.BytesIO(document.archive_bytes), "r")
    else:
        source_handle = zipfile.ZipFile(document.path, "r")
    temp_path = _temporary_output_path(output_path)
    try:
        with source_handle as source_archive:
            source_names = source_archive.namelist()
            with zipfile.ZipFile(temp_path, "w") as output_archive:
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
                            format_warnings=format_warnings,
                            slot_replacements=slot_replacements,
                        )

                    output_archive.writestr(_clone_zip_info(info), raw)
        _validate_output_archive(temp_path, source_names)
        os.replace(temp_path, output_path)
        return format_warnings
    finally:
        temp_path.unlink(missing_ok=True)


def _write_format_report(output_path: Path, records: list[dict[str, object]]) -> None:
    report_path = output_path.with_suffix(".format-warnings.json")
    temp_path: Path | None = None
    try:
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict) or existing.get("kind") != _REPORT_KIND:
                _LOGGER.warning("EPUB format report path belongs to another file: %s", report_path)
                return
        if not records:
            if report_path.exists():
                report_path.unlink()
            return
        temp_path = _temporary_output_path(report_path)
        temp_path.write_text(
            json.dumps(
                {
                    "kind": _REPORT_KIND,
                    "version": 1,
                    "epub": output_path.name,
                    "message": (
                        "Some inline translation boundaries could not be mapped. "
                        "Text was preserved without guessing localized formatting. "
                        "Review the listed blocks in the EPUB editor."
                    ),
                    "warnings": records,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, report_path)
    except (OSError, ValueError) as exc:
        _LOGGER.warning("EPUB was saved, but its format report could not be updated: %s", exc)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                _LOGGER.warning("Could not remove a temporary EPUB format report: %s", exc)


def _temporary_output_path(output_path: Path) -> Path:
    handle = tempfile.NamedTemporaryFile(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    handle.close()
    return Path(handle.name)


def _validate_output_archive(path: Path, expected_names: list[str]) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        if archive.namelist() != expected_names:
            raise ValueError("EPUB output does not preserve the source archive entries")
        corrupt_entry = archive.testzip()
        if corrupt_entry is not None:
            raise ValueError(f"EPUB output contains a corrupt entry: {corrupt_entry}")
        package = parse_package(archive)
        read_archive_entry(archive, package.opf_path)
        for doc_path in package.spine_paths:
            read_archive_entry(archive, doc_path)
        if package.nav_path:
            read_archive_entry(archive, package.nav_path)
        if package.ncx_path:
            read_archive_entry(archive, package.ncx_path)


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
    format_warnings: list[dict[str, object]] | None = None,
    slot_replacements: dict[int, tuple[str, ...]] | None = None,
) -> bytes:
    root = _parse_doc(raw, doc_path)
    elem_by_path = build_elem_by_path(root)
    block_refs: list[tuple[etree._Element, etree._Element]] = []
    inserted_block_paths: set[str] = set()
    allow_bilingual = bilingual and not _is_nav_or_metadata_doc(doc_path, root, segments)
    original_root = copy.deepcopy(root) if allow_bilingual else None
    original_paths = build_elem_by_path(original_root) if original_root is not None else {}
    translated_rubies: set[etree._Element] = set()

    for segment in segments:
        translation = _xml_compatible_text(translations[segment.index])
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

            if slot_replacements is not None:
                for (slot, elem), text in zip(resolved, slot_replacements[segment.index], strict=True):
                    _set_slot(slot, elem, _xml_compatible_text(text))
                continue

            should_insert_bilingual_block = allow_bilingual and (
                not dedup_when_same or segment.text != translation
            )
            if should_insert_bilingual_block and segment.block_path not in inserted_block_paths:
                block = _resolve_elem(root, elem_by_path, segment.block_path)
                if block is not None:
                    original = original_paths[segment.block_path]
                    block_refs.append((block, copy.deepcopy(original)))
                    inserted_block_paths.add(segment.block_path)

            if translation == segment.text:
                continue
            unmapped = _replace_text_slots(resolved, current_texts, translated_lines, translation)
            if unmapped:
                record = {
                    "doc_path": doc_path,
                    "block_path": segment.block_path,
                    "segment_index": segment.index,
                    "source_slots": len(resolved),
                    "translated_lines": len(translated_lines),
                    "reason": "inline_format_boundary_unmapped",
                }
                if format_warnings is not None:
                    format_warnings.append(record)
                _LOGGER.warning(
                    "EPUB inline formatting needs review: %s %s (segment %s)",
                    doc_path, segment.block_path, segment.index,
                )
            if _segment_uses_ruby(segment):
                for _, elem in resolved:
                    translated_rubies.update(
                        node for node in (elem, *elem.iterancestors())
                        if isinstance(node.tag, str) and local_name(node.tag) == "ruby"
                    )

    for ruby in translated_rubies:
        _remove_ruby_annotations(ruby)

    if allow_bilingual:
        cloned_blocks = {block for block, _ in block_refs}
        for block, clone in reversed(block_refs):
            if any(ancestor in cloned_blocks for ancestor in block.iterancestors()):
                continue
            parent = block.getparent()
            if parent is None:
                continue
            _mark_bilingual_clone(clone)
            parent.insert(parent.index(block), clone)
            # A block's tail belongs to its parent, not to the original-text copy.
            clone.tail = "\n"

    return _serialize_doc(root, doc_path)


def _slot_owner(slot: str, elem: etree._Element) -> etree._Element:
    return elem if slot == "text" else elem.getparent()


def _set_slot(slot: str, elem: etree._Element, text: str) -> None:
    if slot == "text":
        elem.text = text
    else:
        elem.tail = text


def _common_container(resolved: list[tuple[str, etree._Element]]) -> etree._Element:
    owners = [_slot_owner(slot, elem) for slot, elem in resolved]
    common = owners[0]
    for owner in owners[1:]:
        ancestors = {owner, *owner.iterancestors()}
        while common not in ancestors:
            common = common.getparent()
    return common


def _initial_scope(
    resolved: list[tuple[str, etree._Element]],
    current_texts: list[str],
) -> tuple[etree._Element, list[int]] | None:
    first = next((i for i, text in enumerate(current_texts) if text.strip()), None)
    if first is None or resolved[first][0] != "text":
        return None
    carrier = resolved[first][1]
    inline_tags = {"span", "em", "i", "strong", "b", "font"}
    if local_name(carrier.tag) not in inline_tags:
        return None

    def scope_indices(node: etree._Element) -> list[int]:
        return [
            i for i, (slot, elem) in enumerate(resolved)
            if _slot_owner(slot, elem) is node or node in _slot_owner(slot, elem).iterancestors()
        ]

    indices = scope_indices(carrier)
    prefix = "".join(current_texts[i] for i in indices).strip()
    if not _INITIAL_PATTERN.fullmatch(prefix) or not any(char.isalpha() for char in prefix):
        return None
    while carrier.getparent() is not None and local_name(carrier.getparent().tag) in inline_tags:
        parent = carrier.getparent()
        parent_indices = scope_indices(parent)
        parent_prefix = "".join(current_texts[i] for i in parent_indices).strip()
        if not _INITIAL_PATTERN.fullmatch(parent_prefix):
            break
        carrier, indices = parent, parent_indices
    if any(
        isinstance(e.tag, str) and local_name(e.tag) in {
            "a", "sup", "sub", "ruby", "img", "svg", "code", "pre", "script", "style",
        }
        for e in carrier.iter()
    ):
        return None
    if not any(text.strip() for i, text in enumerate(current_texts) if i not in indices):
        return None
    parent = carrier.getparent()
    if (parent.text or "").strip() or any(
        "".join(sibling.itertext()).strip() or (sibling.tail or "").strip()
        for sibling in carrier.itersiblings(preceding=True)
        if isinstance(sibling.tag, str)
    ):
        return None
    return carrier, indices


def _split_initial(text: str) -> tuple[str, str, str]:
    match = regex.match(r'^(\s*)([\p{Pi}\p{Ps}\x22\x27]*\X)', text, regex.VERSION1)
    if match is None:
        return text, "", ""
    return match[1], match[2], text[match.end():]


def _insert_before(carrier: etree._Element, text: str) -> None:
    previous = carrier.getprevious()
    if previous is None:
        parent = carrier.getparent()
        parent.text = (parent.text or "") + text
    else:
        previous.tail = (previous.tail or "") + text


def _replace_text_slots(
    resolved: list[tuple[str, etree._Element]],
    current_texts: list[str],
    translated_lines: list[str],
    translation: str,
) -> bool:
    if not resolved:
        return False
    common = _common_container(resolved)
    initial = _initial_scope(resolved, current_texts)
    initial_indices = initial[1] if initial else []
    unmapped = any(
        i not in initial_indices and text.strip()
        and (_slot_owner(slot, elem) is not common or (isinstance(elem.tag, str) and local_name(elem.tag) == "br"))
        for i, ((slot, elem), text) in enumerate(zip(resolved, current_texts, strict=True))
    )
    if len(translated_lines) == len(resolved):
        for (slot, elem), text in zip(resolved, translated_lines, strict=True):
            _set_slot(slot, elem, text)
        empty_inline = any(
            i not in initial_indices and current_texts[i].strip() and not text.strip()
            and _slot_owner(slot, elem) is not common
            for i, ((slot, elem), text) in enumerate(zip(resolved, translated_lines, strict=True))
        )
        if initial:
            carrier, indices = initial
            prefix = "".join(translated_lines[i] for i in indices)
            if not prefix.strip():
                donor = next((i for i in range(max(indices) + 1, len(resolved)) if translated_lines[i].strip()), None)
                if donor is not None:
                    leading, prefix, remainder = _split_initial(translated_lines[donor])
                    _set_slot(*resolved[donor], leading + remainder)
            leading, prefix, remainder = _split_initial(prefix)
            for i in indices:
                _set_slot(*resolved[i], "")
            _set_slot(*resolved[indices[0]], prefix)
            _insert_before(carrier, leading)
            carrier.tail = remainder + (carrier.tail or "")
        return empty_inline

    # Missing boundaries cannot identify which translated words deserve emphasis.
    # Keep the complete text in the shared container, never in a partial inline span.
    for slot, elem in resolved:
        _set_slot(slot, elem, "")
    if initial:
        carrier, indices = initial
        leading, prefix, remainder = _split_initial(translation)
        _set_slot(*resolved[indices[0]], prefix)
        remainder_carrier = carrier
        while remainder_carrier.getparent() is not common:
            remainder_carrier = remainder_carrier.getparent()
        _insert_before(remainder_carrier, leading)
        remainder_carrier.tail = remainder + (remainder_carrier.tail or "")
    else:
        slot, elem = resolved[0]
        owner = _slot_owner(slot, elem)
        if owner is common:
            _set_slot(slot, elem, translation)
        else:
            carrier = owner
            while carrier.getparent() is not common:
                carrier = carrier.getparent()
            _insert_before(carrier, translation)
    return unmapped


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
            if elem.tail:
                previous = elem.getprevious()
                if previous is None:
                    parent.text = (parent.text or "") + elem.tail
                else:
                    previous.tail = (previous.tail or "") + elem.tail
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
    for elem in clone.iter():
        if not isinstance(elem.tag, str):
            continue
        elem.attrib.pop("id", None)
        elem.attrib.pop("{http://www.w3.org/XML/1998/namespace}id", None)
        if local_name(elem.tag) == "a":
            elem.attrib.pop("name", None)
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


_INVALID_XML_CHARACTERS = re.compile(
    "[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)


def _xml_compatible_text(text: str) -> str:
    return _INVALID_XML_CHARACTERS.sub("", text)
