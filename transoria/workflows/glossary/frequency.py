"""Frequency counting + reference collection with longest-term masking.

Literal candidates use plain ``str.replace`` for masking. Regex candidates
use ``re.sub`` with the same sentinel string; the masking sentinel is a
non-printable control character of equal length to the matched span so the
downstream string scans can't accidentally re-match the masked region.

Per ``docs/glossary-extraction-module-design.md`` §"Frequency and Reference
Search":

1. Sort glossary entries by descending source term length.
2. Scan source text segments for each term.
3. Collect reference lines containing the term (capped at the configured
   ``reference_example_limit``).
4. Count frequency = number of segments containing at least one occurrence
   of the term (we use reference-line count, not raw occurrences — it
   matches the masking step's intent and is more robust against filler
   tokens that appear many times per line).
5. Mask already matched longer terms in scanned text so child terms
   contained in a parent term don't double-count the parent's occurrences.
6. Remove entries below the configured ``min_frequency``.
7. Sort the final list by frequency descending; ties broken by descending
   source length, then by ``src`` lexicographically for reproducible output.
"""

from __future__ import annotations

import re
from typing import Sequence

from transoria.workflows.glossary.candidate import Candidate, GlossaryRecord


# The sentinel is a single non-printable control character that no realistic
# glossary entry would contain. Using a *letter*-bearing sentinel (e.g.
# ``__GMASK_0__``) silently re-introduces matches: a candidate "A" would
# find the "A" inside "GMASK" and inflate its frequency.
_MASK_CHARACTER = "\u0001"


def count_frequencies_and_references(
    candidates: Sequence[Candidate],
    source_segments: Sequence[str],
    *,
    reference_example_limit: int,
    min_frequency: int,
) -> tuple[GlossaryRecord, ...]:
    """Compute frequency + references for each candidate.

    ``source_segments`` is the ordered list of original source lines for one
    novel, exactly as a human would read them. References are taken from
    this list (unmasked) so the references TXT shows real source context.
    """

    ordered = sorted(candidates, key=lambda candidate: -len(candidate.src))
    masked = list(source_segments)

    records: list[GlossaryRecord] = []
    for candidate in ordered:
        if not candidate.src:
            continue
        match_count = 0
        references: list[str] = []
        if candidate.regex:
            try:
                pattern = re.compile(candidate.src)
            except re.error:
                continue
            for segment_index, segment in enumerate(masked):
                if not pattern.search(segment):
                    continue
                match_count += 1
                if len(references) < max(0, reference_example_limit):
                    references.append(source_segments[segment_index])

                def _replace_with_sentinel(match: re.Match[str]) -> str:
                    return _MASK_CHARACTER * len(match.group(0))

                masked[segment_index] = pattern.sub(
                    _replace_with_sentinel, segment
                )
        else:
            sentinel = _MASK_CHARACTER * len(candidate.src)
            for segment_index, segment in enumerate(masked):
                if candidate.src not in segment:
                    continue
                match_count += 1
                if len(references) < max(0, reference_example_limit):
                    references.append(source_segments[segment_index])
                masked[segment_index] = segment.replace(candidate.src, sentinel)
        if match_count < min_frequency:
            continue
        records.append(
            GlossaryRecord(
                src=candidate.src,
                dst=candidate.dst,
                info=candidate.info,
                regex=candidate.regex,
                frequency=match_count,
                references=tuple(references),
            )
        )

    records.sort(
        key=lambda record: (-record.frequency, -len(record.src), record.src)
    )
    return tuple(records)


__all__ = ["count_frequencies_and_references"]
