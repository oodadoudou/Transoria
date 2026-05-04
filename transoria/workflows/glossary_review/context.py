"""Reference context extraction for glossary review."""

from __future__ import annotations

import re

from transoria.workflows.glossary_review.loader import GlossaryReviewRow


def attach_reference_contexts(
    rows: tuple[GlossaryReviewRow, ...],
    reference_text: str,
    *,
    max_examples: int = 3,
    window: int = 80,
) -> tuple[GlossaryReviewRow, ...]:
    return tuple(
        GlossaryReviewRow(
            row_index=row.row_index,
            src=row.src,
            dst=row.dst,
            info=row.info,
            frequency=row.frequency,
            context=_context_for_term(reference_text, row.src, max_examples, window),
            deleted=row.deleted,
        )
        for row in rows
    )


def _context_for_term(text: str, term: str, max_examples: int, window: int) -> str:
    if not term:
        return ""
    excerpts: list[str] = []
    for match in re.finditer(re.escape(term), text):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        excerpt = text[start:end].replace("\r", " ").replace("\n", " ")
        excerpt = re.sub(r"\s+", " ", excerpt).strip()
        if excerpt and excerpt not in excerpts:
            excerpts.append(excerpt)
        if len(excerpts) >= max_examples:
            break
    return "\n".join(excerpts)


__all__ = ["attach_reference_contexts"]
