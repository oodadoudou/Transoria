"""Three-artifact glossary writers (XLSX, JSON, references TXT).

All three files share the hyphen-separated naming convention required by
``docs/glossary-extraction-module-design.md``:

- ``<NovelName>-Glossary.xlsx``
- ``<NovelName>-Glossary.json``
- ``<NovelName>-Glossary-references.txt``

The XLSX columns are ``src``, ``dst``, ``info``, ``regex``, ``frequency``;
the JSON content matches column-for-column; and the references TXT uses the
Chinese-labeled block format described in the design doc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from openpyxl import Workbook

from transoria.workflows.glossary.candidate import GlossaryRecord


GLOSSARY_FILENAME_XLSX_SUFFIX = "-Glossary.xlsx"
GLOSSARY_FILENAME_JSON_SUFFIX = "-Glossary.json"
GLOSSARY_FILENAME_REFERENCES_SUFFIX = "-Glossary-references.txt"
GLOSSARY_FILENAME_DECODE_ISSUES_SUFFIX = "-Glossary-decode-issues.txt"

# Re-exported for tests/external callers that want to assert filename layout.
GLOSSARY_FILENAME_XLSX = GLOSSARY_FILENAME_XLSX_SUFFIX
GLOSSARY_FILENAME_JSON = GLOSSARY_FILENAME_JSON_SUFFIX
GLOSSARY_FILENAME_REFERENCES = GLOSSARY_FILENAME_REFERENCES_SUFFIX
GLOSSARY_FILENAME_DECODE_ISSUES = GLOSSARY_FILENAME_DECODE_ISSUES_SUFFIX

XLSX_COLUMNS: tuple[str, ...] = ("src", "dst", "info", "regex", "frequency")

_REFERENCE_SEPARATOR = "※" * 24


def glossary_basename(source_path: Path) -> str:
    """Hyphenated stem used for all three glossary artifacts.

    For ``Novel Name.epub`` the basename is ``Novel Name``; the writers
    append the artifact-specific suffix.
    """

    return source_path.stem


def write_glossary_xlsx(
    records: Sequence[GlossaryRecord],
    output_dir: Path,
    *,
    basename: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{basename}{GLOSSARY_FILENAME_XLSX_SUFFIX}"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Glossary"
    sheet.append(list(XLSX_COLUMNS))
    for record in records:
        sheet.append(
            [
                record.src,
                record.dst,
                record.info,
                record.regex,
                record.frequency,
            ]
        )
    # Enable Excel auto-filter on the header so users can sort and
    # filter without setting it up themselves. Range covers all
    # populated rows (header + data).
    last_col_letter = chr(ord("A") + len(XLSX_COLUMNS) - 1)
    last_row = 1 + len(records)
    sheet.auto_filter.ref = f"A1:{last_col_letter}{last_row}"
    sheet.freeze_panes = "A2"
    workbook.save(path)
    return path


def write_glossary_json(
    records: Sequence[GlossaryRecord],
    output_dir: Path,
    *,
    basename: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{basename}{GLOSSARY_FILENAME_JSON_SUFFIX}"
    payload = [record.to_dict() for record in records]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_glossary_references_text(
    records: Sequence[GlossaryRecord],
    output_dir: Path,
    *,
    basename: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{basename}{GLOSSARY_FILENAME_REFERENCES_SUFFIX}"
    blocks: list[str] = []
    for record in records:
        block_lines = [
            f"原文: {record.src}",
            f"译文: {record.dst}",
            f"备注: {record.info}",
            f"出现次数: {record.frequency}",
            f"参考文本: {_REFERENCE_SEPARATOR}",
        ]
        block_lines.extend(record.references)
        blocks.append("\n".join(block_lines))
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")
    return path


def write_glossary_artifacts(
    records: Sequence[GlossaryRecord],
    output_dir: Path,
    *,
    source_path: Path | None = None,
    basename: str | None = None,
) -> tuple[Path, Path, Path]:
    """Write all three artifacts for one source novel.

    Either ``source_path`` (file-attributed export) or ``basename`` (folder-
    level combined export) must be provided. Passing both is an error.
    """

    if (source_path is None) == (basename is None):
        raise ValueError("Pass exactly one of source_path or basename")
    resolved = basename if basename is not None else glossary_basename(source_path)  # type: ignore[arg-type]
    xlsx_path = write_glossary_xlsx(records, output_dir, basename=resolved)
    json_path = write_glossary_json(records, output_dir, basename=resolved)
    references_path = write_glossary_references_text(
        records, output_dir, basename=resolved
    )
    return xlsx_path, json_path, references_path


def write_glossary_decode_issues(
    issues: Sequence[Mapping[str, str]],
    output_dir: Path,
    *,
    basename: str,
) -> Path | None:
    """Write per-line decode issues. Returns ``None`` when no issues exist."""

    if not issues:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{basename}{GLOSSARY_FILENAME_DECODE_ISSUES_SUFFIX}"
    blocks: list[str] = []
    for issue in issues:
        blocks.append(
            "\n".join(
                [
                    f"reason: {issue.get('reason', '')}",
                    f"line: {issue.get('line', '')}",
                ]
            )
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path


__all__ = [
    "GLOSSARY_FILENAME_DECODE_ISSUES",
    "GLOSSARY_FILENAME_JSON",
    "GLOSSARY_FILENAME_REFERENCES",
    "GLOSSARY_FILENAME_XLSX",
    "XLSX_COLUMNS",
    "glossary_basename",
    "write_glossary_artifacts",
    "write_glossary_decode_issues",
    "write_glossary_json",
    "write_glossary_references_text",
    "write_glossary_xlsx",
]
