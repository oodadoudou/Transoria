from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from transoria.formats.epub_parser import parse_epub_file
from transoria.formats.epub_writer import write_epub_to_path
from transoria.formats.text import parse_txt_file


REPLACED_SUFFIX = "-Replaced"


@dataclass(frozen=True)
class ReplacementRule:
    src: str
    dst: str
    regex: bool = False
    case_sensitive: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class ReplacementApplyResult:
    text: str
    replacement_count: int
    errors: list[str]


@dataclass(frozen=True)
class FileReplacementResult:
    output_path: Path
    replacement_count: int
    errors: list[str]


def load_replacement_rules_txt(path: Path) -> list[ReplacementRule]:
    if path.suffix.lower() != ".txt":
        raise ValueError(f"Only .txt replacement rule files are supported: {path}")

    # Use ``parse_txt_file`` so users can drop legacy-encoded rule files
    # (cp949, gbk, shift_jis, …) without having to convert them first;
    # the txt parser already runs the BOM/UTF → chardet → candidate-list
    # cascade and rejects Western single-byte false positives.
    document = parse_txt_file(path)

    rules: list[ReplacementRule] = []
    for line_number, segment in enumerate(document.segments, start=1):
        line = segment.text.strip()
        if line == "" or line.startswith("#"):
            continue
        if "->" not in line:
            raise ValueError(
                f"Malformed replacement rule at line {line_number}: expected `original phrase->new phrase`"
            )

        src, dst = line.split("->", 1)
        src = src.strip()
        dst = dst.strip()
        if src == "":
            raise ValueError(
                f"Malformed replacement rule at line {line_number}: src cannot be empty"
            )

        rules.append(ReplacementRule(src=src, dst=dst))
    return rules


def apply_rules(text: str, rules: list[ReplacementRule]) -> ReplacementApplyResult:
    current = text
    replacement_count = 0
    errors: list[str] = []

    for index, rule in enumerate(rules, start=1):
        if not rule.enabled:
            continue

        flags = 0 if rule.case_sensitive else re.IGNORECASE
        pattern = rule.src if rule.regex else re.escape(rule.src)
        try:
            current, count = re.subn(pattern, rule.dst, current, flags=flags)
        except re.error as exc:
            errors.append(f"Invalid regex in rule {index}: {exc}")
            continue
        replacement_count += count

    return ReplacementApplyResult(text=current, replacement_count=replacement_count, errors=errors)


def replace_txt_file(source_path: Path, output_dir: Path, rules: list[ReplacementRule]) -> FileReplacementResult:
    document = parse_txt_file(source_path)
    original_text = "".join(f"{segment.text}{segment.newline}" for segment in document.segments)
    result = apply_rules(original_text, rules)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _replaced_filename(source_path)
    output_path.write_text(result.text, encoding="utf-8")
    return FileReplacementResult(
        output_path=output_path,
        replacement_count=result.replacement_count,
        errors=result.errors,
    )


def replace_epub_file(source_path: Path, output_dir: Path, rules: list[ReplacementRule]) -> FileReplacementResult:
    document = parse_epub_file(source_path)
    translations: dict[int, str] = {}
    replacement_count = 0
    errors: list[str] = []

    for segment in document.segments:
        lines = segment.text.split("\n")
        replaced_lines: list[str] = []
        segment_count = 0
        for line in lines:
            result = apply_rules(line, rules)
            replaced_lines.append(result.text)
            segment_count += result.replacement_count
            errors.extend(result.errors)
        if segment_count > 0:
            translations[segment.index] = "\n".join(replaced_lines)
            replacement_count += segment_count

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _replaced_filename(source_path)
    write_epub_to_path(document, translations, output_path)
    return FileReplacementResult(output_path=output_path, replacement_count=replacement_count, errors=errors)


def _replaced_filename(source_path: Path) -> str:
    return f"{source_path.stem}{REPLACED_SUFFIX}{source_path.suffix}"


