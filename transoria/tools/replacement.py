from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import zipfile

from transoria.formats.epub_parser import (
    find_by_path,
    parse_epub_file,
    parse_ncx_xml,
    parse_xhtml_or_html,
    read_archive_entry,
)
from transoria.formats.epub_writer import write_epub_slots_to_path
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
    """Apply ``rules`` sequentially to ``text``; each rule sees the previous rule's output."""

    result = _apply_rules_to_slots(
        [text],
        [[0]],
        rules,
        collect_occurrences=collect_occurrences,
        occurrence_limit_per_rule=occurrence_limit_per_rule,
        context_chars=context_chars,
    )
    return ReplacementApplyResult(
        text=result.slots[0],
        replacement_count=result.counts[0],
        errors=result.errors,
        occurrences=result.occurrences,
    )


@dataclass(frozen=True)
class _PreparedRule:
    index: int
    rule: ReplacementRule
    # None means an exact literal handled with str.replace.
    pattern: re.Pattern[str] | None


@dataclass(frozen=True)
class _SlotsResult:
    slots: list[str]
    counts: list[int]
    errors: list[str]
    occurrences: tuple[ReplacementOccurrence, ...]


def _prepare_rules(rules: list[ReplacementRule]) -> tuple[list[_PreparedRule], list[str]]:
    prepared: list[_PreparedRule] = []
    errors: list[str] = []
    for index, rule in enumerate(rules):
        if not rule.enabled:
            continue
        if not rule.regex:
            if rule.src == "":
                continue
            if rule.case_sensitive or _is_caseless(rule.src):
                prepared.append(_PreparedRule(index=index, rule=rule, pattern=None))
                continue
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(rule.src if rule.regex else re.escape(rule.src), flags)
        except re.error as exc:
            errors.append(f"Invalid regex in rule {index + 1}: {exc}")
            continue
        prepared.append(_PreparedRule(index=index, rule=rule, pattern=pattern))
    return prepared, errors


def _is_caseless(text: str) -> bool:
    return all(char.lower() == char and char.upper() == char for char in text)


def _apply_rules_to_slots(
    slots: list[str],
    groups: list[list[int]],
    rules: list[ReplacementRule],
    *,
    collect_occurrences: bool,
    occurrence_limit_per_rule: int,
    context_chars: int,
) -> _SlotsResult:
    """Run rules in order over independent text slots, rule by rule.

    Slots never interact, so iterating rules outermost keeps each slot's
    sequential semantics while letting a literal rule skip a whole group
    after one substring check on the group's joined text.
    """

    prepared, errors = _prepare_rules(rules)
    slots = list(slots)
    counts = [0] * len(slots)
    collected: list[ReplacementOccurrence] = []
    joined: list[str | None] = [None] * len(groups)

    for item in prepared:
        recorded = 0
        failed = False
        for group_index, members in enumerate(groups):
            if failed:
                break
            if not item.rule.regex:
                text = joined[group_index]
                if text is None:
                    text = "\0".join(slots[i] for i in members)
                    joined[group_index] = text
                if item.pattern is None:
                    if item.rule.src not in text:
                        continue
                elif item.pattern.search(text) is None:
                    continue
            changed = False
            for slot_index in members:
                before = slots[slot_index]
                if collect_occurrences and recorded < occurrence_limit_per_rule:
                    found = _occurrences(item, before, occurrence_limit_per_rule - recorded, context_chars)
                    collected.extend(found)
                    recorded += len(found)
                try:
                    after, count = _substitute(item, before)
                except re.error as exc:
                    errors.append(f"Invalid regex in rule {item.index + 1}: {exc}")
                    failed = True
                    break
                if count:
                    slots[slot_index] = after
                    counts[slot_index] += count
                    changed = True
            if changed:
                joined[group_index] = None

    return _SlotsResult(
        slots=slots, counts=counts, errors=errors, occurrences=tuple(collected)
    )


def _substitute(item: _PreparedRule, text: str) -> tuple[str, int]:
    if item.pattern is None:
        count = text.count(item.rule.src)
        return (text.replace(item.rule.src, item.rule.dst), count) if count else (text, 0)
    if item.rule.regex:
        return item.pattern.subn(item.rule.dst, text)
    dst = item.rule.dst
    return item.pattern.subn(lambda _match: dst, text)


def _occurrences(
    item: _PreparedRule, text: str, limit: int, context_chars: int
) -> list[ReplacementOccurrence]:
    if item.pattern is None:
        src = item.rule.src
        spans: list[tuple[int, int]] = []
        start = text.find(src)
        while start >= 0 and len(spans) < limit:
            spans.append((start, start + len(src)))
            start = text.find(src, start + len(src))
    else:
        spans = []
        for match in item.pattern.finditer(text):
            if len(spans) >= limit:
                break
            spans.append(match.span())
    return [
        ReplacementOccurrence(
            rule_index=item.index,
            char_offset=start,
            before_context=text[max(0, start - context_chars):start],
            match_text=text[start:end],
            after_context=text[end:end + context_chars],
            replacement_text=item.rule.dst,
        )
        for start, end in spans
    ]


def replace_txt_file(
    source_path: Path,
    output_dir: Path,
    rules: list[ReplacementRule],
    *,
    collect_occurrences: bool = False,
    in_place: bool = False,
) -> FileReplacementResult:
    document = parse_txt_file(source_path)
    original_text = "".join(f"{segment.text}{segment.newline}" for segment in document.segments)
    result = apply_rules(
        original_text, rules, collect_occurrences=collect_occurrences
    )
    if in_place:
        output_path = source_path
        if result.replacement_count > 0:
            _overwrite_text(source_path, result.text, document.encoding)
    else:
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
    in_place: bool = False,
) -> FileReplacementResult:
    document = parse_epub_file(source_path, buffer_archive=True)
    replacements: dict[int, tuple[str, ...]] = {}
    replacement_count = 0
    errors: list[str] = []
    occurrences: list[ReplacementOccurrence] = []
    with zipfile.ZipFile(io.BytesIO(document.archive_bytes)) as archive:
        roots = {}
        for path in dict.fromkeys(segment.doc_path for segment in document.segments):
            raw = read_archive_entry(archive, path)
            roots[path] = parse_ncx_xml(raw) if path.lower().endswith(".ncx") else parse_xhtml_or_html(raw)

    slots: list[str] = []
    segment_slots: list[list[int]] = []
    doc_groups: dict[str, list[int]] = {}
    for segment in document.segments:
        members: list[int] = []
        for part in segment.parts:
            elem = find_by_path(roots[segment.doc_path], part.path)
            if elem is None:
                raise ValueError("EPUB replacement cannot resolve the source text slot")
            members.append(len(slots))
            slots.append((elem.text if part.slot == "text" else elem.tail) or "")
        segment_slots.append(members)
        doc_groups.setdefault(segment.doc_path, []).extend(members)

    result = _apply_rules_to_slots(
        slots,
        list(doc_groups.values()),
        rules,
        collect_occurrences=collect_occurrences,
        occurrence_limit_per_rule=DEFAULT_OCCURRENCE_LIMIT_PER_RULE,
        context_chars=DEFAULT_CONTEXT_CHARS,
    )
    errors.extend(result.errors)
    occurrences.extend(result.occurrences)
    for segment, members in zip(document.segments, segment_slots, strict=True):
        segment_count = sum(result.counts[i] for i in members)
        if segment_count > 0:
            replacements[segment.index] = tuple(result.slots[i] for i in members)
            replacement_count += segment_count

    if in_place:
        output_path = source_path
        if replacement_count > 0:
            write_epub_slots_to_path(document, replacements, output_path)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / _replaced_filename(source_path)
        write_epub_slots_to_path(document, replacements, output_path)
    return FileReplacementResult(
        output_path=output_path,
        replacement_count=replacement_count,
        errors=errors,
        occurrences=tuple(occurrences),
    )


def _overwrite_text(path: Path, text: str, encoding: str) -> None:
    """Rewrite a source TXT atomically, keeping its encoding (and BOM) when it can hold the new text."""

    with path.open("rb") as handle:
        has_bom = handle.read(3) == b"\xef\xbb\xbf"
    target = "utf-8-sig" if has_bom and encoding.lower() in {"utf-8", "utf8"} else encoding or "utf-8"
    try:
        data = text.encode(target)
    except (LookupError, UnicodeEncodeError):
        data = text.encode("utf-8")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _replaced_filename(source_path: Path) -> str:
    return f"{source_path.stem}{REPLACED_SUFFIX}{source_path.suffix}"
