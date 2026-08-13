from __future__ import annotations

import re

from transoria.workflows.glossary_review.context import attach_reference_contexts
from transoria.workflows.glossary_review.loader import GlossaryReviewRow


def test_attach_reference_contexts_matches_legacy_extraction() -> None:
    text = (
        "the he hero walked into the workshop\n"
        "then the art workshop closed\r\n"
        "he returned to another workshop"
    )
    rows = tuple(
        GlossaryReviewRow(index, term, "译文", "类型", 1)
        for index, term in enumerate(("he", "the", "workshop", "missing"), start=2)
    )

    attached = attach_reference_contexts(rows, text, max_examples=3, window=8)

    assert [row.context for row in attached] == [
        _legacy_context(text, row.src, max_examples=3, window=8) for row in rows
    ]


def test_attach_reference_contexts_reuses_context_for_duplicate_terms() -> None:
    rows = (
        GlossaryReviewRow(2, "term", "甲", "类型", 1),
        GlossaryReviewRow(3, "term", "乙", "类型", 1),
    )

    attached = attach_reference_contexts(rows, "term first; term second")

    assert attached[0].context == attached[1].context
    assert attached[0].dst == "甲"
    assert attached[1].dst == "乙"


def _legacy_context(text: str, term: str, *, max_examples: int, window: int) -> str:
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
