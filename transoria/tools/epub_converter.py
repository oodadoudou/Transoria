from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
import zipfile

from lxml import etree

from transoria.formats.epub_parser import (
    is_html_document_path,
    iter_children_elements,
    local_name,
    parse_package,
    parse_xhtml_or_html,
    read_archive_entry,
)


_EPUB_SUFFIX = ".epub"
_TXT_SUFFIX = ".txt"
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "caption",
    "dd",
    "details",
    "div",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "p",
    "section",
    "summary",
    "td",
    "th",
}
_SKIP_TAGS = {"script", "style", "noscript"}


@dataclass(frozen=True)
class EpubConvertOptions:
    output_dir: str = ""
    recursive: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "EpubConvertOptions":
        return cls(
            output_dir=str(data.get("output_dir", "")),
            recursive=bool(data.get("recursive", True)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "output_dir": self.output_dir,
            "recursive": self.recursive,
        }


@dataclass(frozen=True)
class EpubConvertAction:
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
    def from_mapping(cls, data: Mapping[str, object]) -> "EpubConvertAction":
        return cls(
            id=str(data.get("id", "")),
            source_path=str(data.get("source_path", "")),
            output_path=str(data.get("output_path", "")),
            selected=bool(data.get("selected", True)),
        )


@dataclass(frozen=True)
class EpubConvertPlan:
    input_path: Path
    mode: str
    output_dir: Path
    actions: tuple[EpubConvertAction, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "mode": self.mode,
            "output_dir": str(self.output_dir),
            "actions": [action.to_dict() for action in self.actions],
            "totals": {"epub_files": len(self.actions)},
        }


@dataclass(frozen=True)
class EpubConvertResult:
    action_id: str
    source_path: str
    output_path: str
    status: str
    segments_written: int = 0
    characters_written: int = 0
    spine_documents: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "status": self.status,
            "segments_written": self.segments_written,
            "characters_written": self.characters_written,
            "spine_documents": self.spine_documents,
            "error": self.error,
        }


def build_epub_convert_plan(
    input_path: Path, *, mode: str, options: EpubConvertOptions
) -> EpubConvertPlan:
    source = input_path.expanduser().resolve()
    output_dir = (
        Path(options.output_dir).expanduser().resolve()
        if options.output_dir.strip()
        else (source.parent if source.is_file() else source)
    )
    if mode == "file":
        if not source.exists() or not source.is_file():
            raise ValueError(f"EPUB file does not exist: {input_path}")
        if source.suffix.lower() != _EPUB_SUFFIX:
            raise ValueError(f"input file must be .epub: {input_path}")
        actions = tuple(
            EpubConvertAction(
                id="epub-0000",
                source_path=str(source),
                output_path=str((output_dir / source.with_suffix(_TXT_SUFFIX).name).resolve()),
            )
        )
    elif mode == "folder":
        if not source.exists() or not source.is_dir():
            raise ValueError(f"input folder does not exist: {input_path}")
        iterator = source.rglob("*") if options.recursive else source.glob("*")
        files = sorted(
            path
            for path in iterator
            if path.is_file() and path.suffix.lower() == _EPUB_SUFFIX
        )
        actions = tuple(
            EpubConvertAction(
                id=f"epub-{index:04d}",
                source_path=str(path),
                output_path=str(_output_path_for_folder(source, output_dir, path)),
            )
            for index, path in enumerate(files)
        )
    else:
        raise ValueError(f"unsupported EPUB convert mode: {mode!r}")
    return EpubConvertPlan(
        input_path=source,
        mode=mode,
        output_dir=output_dir,
        actions=actions,
    )


def convert_epub_to_txt(action: EpubConvertAction) -> EpubConvertResult:
    source = Path(action.source_path).expanduser().resolve()
    requested_output = Path(action.output_path).expanduser().resolve()
    output = requested_output
    try:
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"source EPUB not found: {source}")
        if source.suffix.lower() != _EPUB_SUFFIX:
            raise ValueError(f"source is not an EPUB file: {source}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output = _unique_output(output)
        export = export_epub_text(source)
        output.write_text(export.text, encoding="utf-8", newline="\n")
        return EpubConvertResult(
            action_id=action.id,
            source_path=str(source),
            output_path=str(output),
            status="converted",
            segments_written=export.segments_written,
            characters_written=len(export.text),
            spine_documents=export.spine_documents,
        )
    except Exception as exc:  # noqa: BLE001
        return EpubConvertResult(
            action_id=action.id,
            source_path=str(source),
            output_path=str(requested_output),
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


@dataclass(frozen=True)
class EpubTextExport:
    text: str
    segments_written: int
    spine_documents: int


def export_epub_text(path: Path) -> EpubTextExport:
    documents: list[str] = []
    segments_written = 0
    spine_documents = 0
    with zipfile.ZipFile(path, "r") as archive:
        package = parse_package(archive)
        for doc_path in package.spine_paths:
            if not is_html_document_path(doc_path):
                continue
            text = _render_spine_document(archive, doc_path)
            spine_documents += 1
            if text:
                documents.append(text)
                segments_written += len([line for line in text.split("\n\n") if line.strip()])
    return EpubTextExport(
        text="\n\n".join(documents).rstrip() + ("\n" if documents else ""),
        segments_written=segments_written,
        spine_documents=spine_documents,
    )


def build_epub_convert_report(
    *,
    task_id: str,
    input_path: Path,
    mode: str,
    generated_at: str,
    results: Iterable[EpubConvertResult],
) -> dict[str, object]:
    rows = [result.to_dict() for result in results]
    converted = sum(1 for row in rows if row["status"] == "converted")
    failed = sum(1 for row in rows if row["status"] == "failed")
    return {
        "task_id": task_id,
        "generated_at": generated_at,
        "input_path": str(input_path),
        "mode": mode,
        "totals": {
            "actions": len(rows),
            "converted": converted,
            "failed": failed,
            "segments_written": sum(int(row["segments_written"]) for row in rows),
            "characters_written": sum(int(row["characters_written"]) for row in rows),
            "spine_documents": sum(int(row["spine_documents"]) for row in rows),
        },
        "results": rows,
    }


def _render_spine_document(archive: zipfile.ZipFile, doc_path: str) -> str:
    root = parse_xhtml_or_html(read_archive_entry(archive, doc_path))
    bodies = root.xpath(".//*[local-name()='body']")
    body = bodies[0] if bodies else root
    blocks = _render_block_children(body)
    return "\n\n".join(blocks).rstrip()


def _render_block_children(elem: etree._Element) -> list[str]:
    blocks: list[str] = []
    direct = _normalize_inline_text(elem.text or "")
    if direct.strip():
        blocks.append(direct.strip())
    for child in iter_children_elements(elem):
        blocks.extend(_render_element(child))
        tail = _normalize_inline_text(child.tail or "")
        if tail.strip():
            blocks.append(tail.strip())
    return blocks


def _render_element(elem: etree._Element) -> list[str]:
    tag = local_name(elem.tag)
    if tag in _SKIP_TAGS:
        return []
    if tag == "br":
        return [""]
    if tag in _BLOCK_TAGS:
        text = _render_inline(elem).strip()
        if text:
            return [text]
        if _contains_line_break(elem):
            return [""]
        return _render_block_children(elem)
    return _render_block_children(elem)


def _render_inline(elem: etree._Element) -> str:
    tag = local_name(elem.tag)
    if tag in _SKIP_TAGS:
        return ""
    parts: list[str] = []
    if elem.text:
        parts.append(_normalize_inline_text(elem.text))
    for child in iter_children_elements(elem):
        if local_name(child.tag) == "br":
            parts.append("\n")
        elif local_name(child.tag) in _BLOCK_TAGS:
            nested = _render_inline(child).strip()
            if nested:
                if parts and not parts[-1].endswith(("\n", " ")):
                    parts.append("\n")
                parts.append(nested)
        else:
            parts.append(_render_inline(child))
        if child.tail:
            parts.append(_normalize_inline_text(child.tail))
    return "".join(parts)


def _contains_line_break(elem: etree._Element) -> bool:
    return any(local_name(child.tag) == "br" for child in iter_children_elements(elem))


def _normalize_inline_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _output_path_for_folder(input_dir: Path, output_dir: Path, source: Path) -> Path:
    try:
        relative = source.relative_to(input_dir)
    except ValueError:
        relative = Path(source.name)
    return (output_dir / relative).with_suffix(_TXT_SUFFIX).resolve()


def _unique_output(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot find unique output path for {path}")


__all__ = [
    "EpubConvertAction",
    "EpubConvertOptions",
    "EpubConvertPlan",
    "EpubConvertResult",
    "build_epub_convert_plan",
    "build_epub_convert_report",
    "convert_epub_to_txt",
    "export_epub_text",
]
