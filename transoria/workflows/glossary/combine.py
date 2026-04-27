"""Folder-level glossary combination.

When multiple novels share characters (multi-volume series, anthologies), per-
file glossaries fragment the same name into separate rows. The combiner
merges per-file ``GlossaryRecord`` lists by ``src``: vote-merges ``dst`` /
``info``, sums ``frequency``, and concatenates references with a per-entry
cap. Output has the same row shape so downstream callers (XLSX/JSON/refs
writers) don't change.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable, Sequence

from transoria.workflows.glossary.candidate import GlossaryRecord


def combine_glossary_records(
    per_file_records: Iterable[Sequence[GlossaryRecord]],
    *,
    reference_example_limit: int,
) -> tuple[GlossaryRecord, ...]:
    grouped: dict[tuple[str, bool], list[GlossaryRecord]] = defaultdict(list)
    for records in per_file_records:
        for record in records:
            grouped[(record.src, record.regex)].append(record)

    combined: list[GlossaryRecord] = []
    for (src, regex_flag), group in grouped.items():
        dst = _vote(record.dst for record in group)
        info = _vote(record.info for record in group)
        frequency = sum(record.frequency for record in group)
        references = _round_robin_references(
            [record.references for record in group],
            cap=max(0, reference_example_limit),
        )
        combined.append(
            GlossaryRecord(
                src=src,
                dst=dst,
                info=info,
                regex=regex_flag,
                frequency=frequency,
                references=tuple(references),
            )
        )

    combined.sort(
        key=lambda record: (-record.frequency, -len(record.src), record.src)
    )
    return tuple(combined)


def _round_robin_references(
    per_source_references: list[Sequence[str]], *, cap: int
) -> list[str]:
    """Distribute the reference cap fairly across the contributing sources.

    Picks one reference per source in turn (skipping sources that have run
    out), avoiding duplicates. Without this, a single source's references
    fill the cap and every other source contributes zero.
    """

    if cap <= 0 or not per_source_references:
        return []
    indices = [0] * len(per_source_references)
    chosen: list[str] = []
    seen: set[str] = set()
    while len(chosen) < cap:
        progressed = False
        for source_index, refs in enumerate(per_source_references):
            cursor = indices[source_index]
            while cursor < len(refs) and refs[cursor] in seen:
                cursor += 1
            indices[source_index] = cursor
            if cursor >= len(refs):
                continue
            ref = refs[cursor]
            chosen.append(ref)
            seen.add(ref)
            indices[source_index] = cursor + 1
            progressed = True
            if len(chosen) >= cap:
                break
        if not progressed:
            break
    return chosen


def _vote(values: Iterable[str]) -> str:
    counter = Counter(values)
    counter.pop("", None)
    if not counter:
        return ""
    most_count = max(counter.values())
    candidates = sorted(value for value, count in counter.items() if count == most_count)
    return candidates[0]


__all__ = ["combine_glossary_records"]
