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
class ReplacementOccurrence:
    """One match site captured during ``apply_rules``.

    Used by the post-run report so the user can see exactly where each
    rule fired with surrounding context. Caller (orchestrator) wraps
    each occurrence with the file path it came from before persisting.
    """

    rule_index: int
    char_offset: int
    before_context: str
    match_text: str
    after_context: str
    replacement_text: str


@dataclass(frozen=True)
class ReplacementApplyResult:
    text: str
    replacement_count: int
    errors: list[str]
    occurrences: tuple[ReplacementOccurrence, ...] = ()


@dataclass(frozen=True)
class FileReplacementResult:
    output_path: Path
    replacement_count: int
    errors: list[str]
    occurrences: tuple[ReplacementOccurrence, ...] = ()


# Defaults sized so a typical novel run keeps the report under ~10 MB
# even when many rules each match thousands of times. The cap is per
# rule per *file*; the aggregated report can carry more.
DEFAULT_OCCURRENCE_LIMIT_PER_RULE = 200
DEFAULT_CONTEXT_CHARS = 80


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
        parsed = _split_rule_line(line)
        if parsed is None:
            raise ValueError(
                f"Malformed replacement rule at line {line_number}: expected `original phrase->new phrase`"
            )

        src, dst = parsed
        if src == "":
            raise ValueError(
                f"Malformed replacement rule at line {line_number}: src cannot be empty"
            )

        rules.append(ReplacementRule(src=src, dst=dst))
    return rules


def _split_rule_line(line: str) -> tuple[str, str] | None:
    if "->" not in line:
        return None
    src, dst = (part.strip() for part in line.split("->", 1))
    if src.endswith("#") and dst.startswith("#"):
        src = src[:-1].rstrip()
        dst = dst[1:].lstrip()
    return src, dst


def apply_rules(
    text: str,
    rules: list[ReplacementRule],
    *,
    collect_occurrences: bool = False,
    occurrence_limit_per_rule: int = DEFAULT_OCCURRENCE_LIMIT_PER_RULE,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> ReplacementApplyResult:
    """Apply ``rules`` sequentially to ``text``.

    When ``collect_occurrences=True``, also captures up to
    ``occurrence_limit_per_rule`` match sites per rule with surrounding
    context for the post-run report. Capping prevents a rule that
    matches tens of thousands of times from blowing up the report
    payload; ``truncated`` indication is the caller's job (it knows
    which rules hit the cap).
    """

    current = text
    replacement_count = 0
    errors: list[str] = []
    collected: list[ReplacementOccurrence] = []

    for index, rule in enumerate(rules):
        line_number = index + 1
        if not rule.enabled:
            continue

        flags = 0 if rule.case_sensitive else re.IGNORECASE
        pattern = rule.src if rule.regex else re.escape(rule.src)

        if not collect_occurrences:
            try:
                current, count = re.subn(pattern, rule.dst, current, flags=flags)
            except re.error as exc:
                errors.append(f"Invalid regex in rule {line_number}: {exc}")
                continue
            replacement_count += count
            continue

        # Two-pass when collecting: snapshot the input, find matches
        # with context, then perform the substitution. This avoids the
        # closure-vs-running-text confusion of a callback-based ``re.sub``
        # and keeps offsets aligned with the snapshot the user expects
        # to see in the report ("before this rule fired").
        try:
            compiled = re.compile(pattern, flags=flags)
        except re.error as exc:
            errors.append(f"Invalid regex in rule {line_number}: {exc}")
            continue

        snapshot = current
        for match_index, match in enumerate(compiled.finditer(snapshot)):
            replacement_count += 1
            if match_index >= occurrence_limit_per_rule:
                continue
            start, end = match.span()
            before_start = max(0, start - context_chars)
            after_end = min(len(snapshot), end + context_chars)
            collected.append(
                ReplacementOccurrence(
                    rule_index=index,
                    char_offset=start,
                    before_context=snapshot[before_start:start],
                    match_text=snapshot[start:end],
                    after_context=snapshot[end:after_end],
                    replacement_text=rule.dst,
                )
            )
        try:
            current = compiled.sub(rule.dst, snapshot)
        except re.error as exc:
            errors.append(f"Invalid regex in rule {line_number}: {exc}")
            continue

    return ReplacementApplyResult(
        text=current,
        replacement_count=replacement_count,
        errors=errors,
        occurrences=tuple(collected),
    )


def replace_txt_file(
    source_path: Path,
    output_dir: Path,
    rules: list[ReplacementRule],
    *,
    collect_occurrences: bool = False,
) -> FileReplacementResult:
    document = parse_txt_file(source_path)
    original_text = "".join(f"{segment.text}{segment.newline}" for segment in document.segments)
    result = apply_rules(
        original_text, rules, collect_occurrences=collect_occurrences
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _replaced_filename(source_path)
    output_path.write_text(result.text, encoding="utf-8")
    return FileReplacementResult(
        output_path=output_path,
        replacement_count=result.replacement_count,
        errors=result.errors,
        occurrences=result.occurrences,
    )


def replace_epub_file(
    source_path: Path,
    output_dir: Path,
    rules: list[ReplacementRule],
    *,
    collect_occurrences: bool = False,
) -> FileReplacementResult:
    document = parse_epub_file(source_path)
    translations: dict[int, str] = {}
    replacement_count = 0
    errors: list[str] = []
    occurrences: list[ReplacementOccurrence] = []
    # Per-rule budget across the whole epub so a single chapter can't
    # exhaust the cap and starve later chapters from showing up.
    per_rule_used: dict[int, int] = {}

    for segment in document.segments:
        lines = segment.text.split("\n")
        replaced_lines: list[str] = []
        segment_count = 0
        for line in lines:
            result = apply_rules(line, rules, collect_occurrences=collect_occurrences)
            replaced_lines.append(result.text)
            segment_count += result.replacement_count
            errors.extend(result.errors)
            if collect_occurrences:
                for occ in result.occurrences:
                    used = per_rule_used.get(occ.rule_index, 0)
                    if used >= DEFAULT_OCCURRENCE_LIMIT_PER_RULE:
                        continue
                    per_rule_used[occ.rule_index] = used + 1
                    occurrences.append(occ)
        if segment_count > 0:
            translations[segment.index] = "\n".join(replaced_lines)
            replacement_count += segment_count

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _replaced_filename(source_path)
    write_epub_to_path(document, translations, output_path)
    return FileReplacementResult(
        output_path=output_path,
        replacement_count=replacement_count,
        errors=errors,
        occurrences=tuple(occurrences),
    )


def _replaced_filename(source_path: Path) -> str:
    return f"{source_path.stem}{REPLACED_SUFFIX}{source_path.suffix}"
