from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
import zipfile

from lxml import etree

from transoria.formats.epub_parser import (
    build_elem_path_map,
    build_has_block_descendant_map,
    build_skipped_map,
    collect_document_units,
    parse_container_opf_path,
    parse_manifest,
    parse_spine_paths,
    parse_xhtml_or_html,
    read_archive_entry,
)
from transoria.formats.scanner import scan_input_directory


@dataclass(frozen=True)
class EpubPreflightWarning:
    code: str
    path: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def inspect_epub_directory_for_translation(
    input_dir: Path,
    *,
    limit: int = 20,
) -> tuple[EpubPreflightWarning, ...]:
    warnings: list[EpubPreflightWarning] = []
    try:
        documents = scan_input_directory(input_dir)
    except Exception as exc:
        return (
            EpubPreflightWarning(
                code="inspection_failed",
                path=str(input_dir),
                message=f"Could not inspect EPUB inputs: {exc}",
            ),
        )

    for document in documents:
        if document.format.value != "epub":
            continue
        try:
            warnings.extend(inspect_epub_for_translation(document.path))
        except Exception as exc:
            warnings.append(
                EpubPreflightWarning(
                    code="inspection_failed",
                    path=str(document.path),
                    message=f"Could not inspect EPUB structure: {exc}",
                )
            )
        if len(warnings) >= limit:
            return tuple(warnings[:limit])
    return tuple(warnings)


def inspect_epub_for_translation(path: Path) -> tuple[EpubPreflightWarning, ...]:
    warnings: list[EpubPreflightWarning] = []
    with zipfile.ZipFile(path) as archive:
        opf_path = parse_container_opf_path(archive)
        opf_root = etree.fromstring(read_archive_entry(archive, opf_path))
        opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
        manifest = parse_manifest(opf_root, opf_dir)
        spine_paths = parse_spine_paths(opf_root, manifest)

        if not _has_cover_candidate(opf_root, manifest):
            warnings.append(
                EpubPreflightWarning(
                    code="missing_cover",
                    path=str(path),
                    message="EPUB has no manifest cover declaration.",
                )
            )

        if not spine_paths:
            warnings.append(
                EpubPreflightWarning(
                    code="empty_spine",
                    path=str(path),
                    message="EPUB spine is empty.",
                )
            )
            return tuple(warnings)

        text_counts: list[tuple[str, int]] = []
        missing_paths: list[str] = []
        unreadable_paths: list[str] = []
        names = set(archive.namelist())
        for spine_path in spine_paths:
            if not spine_path.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            if spine_path not in names:
                missing_paths.append(spine_path)
                continue
            try:
                count = _count_translatable_units(archive, spine_path)
            except Exception:
                unreadable_paths.append(spine_path)
                continue
            text_counts.append((spine_path, count))

        if missing_paths:
            warnings.append(
                EpubPreflightWarning(
                    code="missing_spine_item",
                    path=str(path),
                    message="EPUB spine references files that are missing.",
                    details={"examples": missing_paths[:5], "count": len(missing_paths)},
                )
            )
        if unreadable_paths:
            warnings.append(
                EpubPreflightWarning(
                    code="unreadable_spine_item",
                    path=str(path),
                    message="EPUB spine contains files that could not be parsed.",
                    details={"examples": unreadable_paths[:5], "count": len(unreadable_paths)},
                )
            )

        empty_paths = [spine_path for spine_path, count in text_counts if count == 0]
        non_empty_after_first = any(count > 0 for _, count in text_counts[1:])
        if text_counts and text_counts[0][1] == 0 and non_empty_after_first:
            warnings.append(
                EpubPreflightWarning(
                    code="first_spine_empty",
                    path=str(path),
                    message="First spine document has no extractable text.",
                    details={"spine_path": text_counts[0][0]},
                )
            )
        if empty_paths:
            warnings.append(
                EpubPreflightWarning(
                    code="empty_spine_documents",
                    path=str(path),
                    message="Some spine documents have no extractable text.",
                    details={"examples": empty_paths[:5], "count": len(empty_paths)},
                )
            )

    return tuple(warnings)


def _has_cover_candidate(
    opf_root: etree._Element,
    manifest: Mapping[str, Mapping[str, str]],
) -> bool:
    for item_id, item in manifest.items():
        properties = {part.strip() for part in item.get("properties", "").split()}
        if "cover-image" in properties:
            return True
        lowered = f"{item_id} {item.get('path', '')}".lower()
        if any(token in lowered for token in ("cover", "titlepage", "표지", "커버")):
            return True

    for meta in opf_root.xpath(".//*[local-name()='metadata']/*[local-name()='meta']"):
        if (meta.get("name") or "").lower() == "cover":
            content = meta.get("content") or ""
            if content in manifest:
                return True
    return False


def _count_translatable_units(archive: zipfile.ZipFile, doc_path: str) -> int:
    root = parse_xhtml_or_html(read_archive_entry(archive, doc_path))
    path_map = build_elem_path_map(root)
    in_skipped_map = build_skipped_map(root)
    has_block_descendant_map = build_has_block_descendant_map(root)
    units = collect_document_units(
        root,
        root,
        path_map,
        in_skipped_map,
        has_block_descendant_map,
    )
    return sum(1 for _, slots in units if any(raw.strip() for _, raw in slots))
