"""Reference context extraction for glossary review."""

from __future__ import annotations

from collections import deque
import re

from transoria.workflows.glossary_review.loader import GlossaryReviewRow


def attach_reference_contexts(
    rows: tuple[GlossaryReviewRow, ...],
    reference_text: str,
    *,
    max_examples: int = 3,
    window: int = 80,
) -> tuple[GlossaryReviewRow, ...]:
    contexts = _contexts_by_term(
        reference_text,
        tuple(dict.fromkeys(row.src for row in rows if row.src)),
        max_examples=max_examples,
        window=window,
    )
    return tuple(
        GlossaryReviewRow(
            row_index=row.row_index,
            src=row.src,
            dst=row.dst,
            info=row.info,
            frequency=row.frequency,
            context=contexts.get(row.src, ""),
            deleted=row.deleted,
        )
        for row in rows
    )


def _contexts_by_term(
    text: str,
    terms: tuple[str, ...],
    *,
    max_examples: int,
    window: int,
) -> dict[str, str]:
    if not text or not terms or max_examples <= 0:
        return {}

    transitions: list[dict[str, int]] = [{}]
    failures = [0]
    outputs: list[list[int]] = [[]]
    for term_index, term in enumerate(terms):
        state = 0
        for char in term:
            next_state = transitions[state].get(char)
            if next_state is None:
                next_state = len(transitions)
                transitions[state][char] = next_state
                transitions.append({})
                failures.append(0)
                outputs.append([])
            state = next_state
        outputs[state].append(term_index)

    queue: deque[int] = deque(transitions[0].values())
    while queue:
        state = queue.popleft()
        for char, next_state in transitions[state].items():
            queue.append(next_state)
            fallback = failures[state]
            while fallback and char not in transitions[fallback]:
                fallback = failures[fallback]
            failures[next_state] = transitions[fallback].get(char, 0)
            outputs[next_state].extend(outputs[failures[next_state]])

    excerpts: list[list[str]] = [[] for _term in terms]
    next_allowed_start = [0 for _term in terms]
    completed = 0
    state = 0
    for end_index, char in enumerate(text):
        while state and char not in transitions[state]:
            state = failures[state]
        state = transitions[state].get(char, 0)
        for term_index in outputs[state]:
            term_excerpts = excerpts[term_index]
            if len(term_excerpts) >= max_examples:
                continue
            term = terms[term_index]
            start_index = end_index - len(term) + 1
            if start_index < next_allowed_start[term_index]:
                continue
            next_allowed_start[term_index] = end_index + 1
            excerpt = text[
                max(0, start_index - window) : min(len(text), end_index + 1 + window)
            ]
            excerpt = excerpt.replace("\r", " ").replace("\n", " ")
            excerpt = re.sub(r"\s+", " ", excerpt).strip()
            if excerpt and excerpt not in term_excerpts:
                term_excerpts.append(excerpt)
                if len(term_excerpts) == max_examples:
                    completed += 1
        if completed == len(terms):
            break

    return {
        term: "\n".join(term_excerpts)
        for term, term_excerpts in zip(terms, excerpts, strict=True)
    }


__all__ = ["attach_reference_contexts"]
