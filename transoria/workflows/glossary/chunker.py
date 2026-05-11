"""Bounded chunking for glossary extraction.

Each chunk concatenates whole source segments (lines, EPUB blocks, etc.) up
to a configurable budget. Splitting *within* a sentence would either lose
context or strand a proper noun across chunks; we never do it. A chunk is
allowed to exceed the budget when it contains a single segment that's
already over budget, so unusually long lines aren't dropped.

By default the budget is character-count based (``chunk_char_limit``). When
the caller supplies a token counter (any callable
``str -> int``), chunks are sized in tokens instead — this is the right
choice when a real tokenizer (e.g. ``tiktoken``) is configured for the
target model.

The orchestrator emits one subtask per chunk. Each chunk carries the
contributing source file path so the candidate-merge step at the end can
attribute findings to the right output novel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from transoria.domain import Language
from transoria.workflows.prefilter import contains_source_language_script
from transoria.workflows.prefilter import is_translation_skippable


@dataclass(frozen=True)
class GlossaryChunk:
    chunk_id: str
    source_file: Path
    text: str

    def to_payload(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "source_file": str(self.source_file),
            "text": self.text,
        }


_RUBY_TAG_RE = re.compile(
    r"\[ruby\s+text\s*=\s*['\"]?[^'\"]+?['\"]?\](.*?)\[/ruby\]",
    re.IGNORECASE | re.DOTALL,
)
_PAREN_RUBY_RE = re.compile(
    r"(?<=[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af])"
    r"[\(（]([\u3040-\u30ff\uac00-\ud7af]+)[\)）]"
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?…\.\n])\s*")


def clean_glossary_source_text(text: str) -> str:
    """Remove common ruby/reading annotations before glossary extraction."""

    cleaned = _RUBY_TAG_RE.sub(lambda match: match.group(1), text)
    return _PAREN_RUBY_RE.sub("", cleaned)


def build_glossary_chunks(
    source_segments_by_file: Mapping[Path, Sequence[str]],
    *,
    chunk_char_limit: int,
    chunk_token_limit: int = 0,
    token_counter: Callable[[str], int] | None = None,
    source_language: Language | None = None,
) -> tuple[GlossaryChunk, ...]:
    """Build chunks across all input files, in deterministic file order.

    ``source_segments_by_file`` maps each parsed source path to its list of
    non-empty segment texts. Empty/whitespace-only segments must be filtered
    by the caller — this function trusts the iteration order it's given.

    When ``token_counter`` and a positive ``chunk_token_limit`` are supplied,
    chunks are sized by token count. Otherwise the function falls back to
    character count using ``chunk_char_limit``.
    """

    use_tokens = token_counter is not None and chunk_token_limit > 0
    if not use_tokens and chunk_char_limit <= 0:
        raise ValueError("chunk_char_limit must be positive when no token counter is given")

    def cost(text: str) -> int:
        if use_tokens:
            return token_counter(text)  # type: ignore[misc]
        return len(text)

    budget = chunk_token_limit if use_tokens else chunk_char_limit

    chunks: list[GlossaryChunk] = []
    chunk_index = 0
    for source_file, segments in source_segments_by_file.items():
        buffer: list[str] = []
        buffer_cost = 0
        for raw_segment in segments:
            if not raw_segment:
                continue
            segment = clean_glossary_source_text(raw_segment)
            if not segment:
                continue
            # Lines made entirely of digits / punctuation / symbols
            # carry no proper-noun candidates worth extracting; drop
            # them before they pad chunks and inflate token cost.
            if is_translation_skippable(segment):
                continue
            if source_language is not None and not contains_source_language_script(
                segment, source_language
            ):
                continue
            for piece in _split_oversized_segment(segment, budget=budget, cost=cost):
                join_cost = 1 if buffer else 0
                segment_cost = cost(piece)
                additional = segment_cost + join_cost
                if buffer and buffer_cost + additional > budget:
                    chunks.append(
                        GlossaryChunk(
                            chunk_id=f"chunk-{chunk_index:05d}",
                            source_file=source_file,
                            text="\n".join(buffer),
                        )
                    )
                    chunk_index += 1
                    buffer = []
                    buffer_cost = 0
                    additional = segment_cost
                buffer.append(piece)
                buffer_cost += additional
        if buffer:
            chunks.append(
                GlossaryChunk(
                    chunk_id=f"chunk-{chunk_index:05d}",
                    source_file=source_file,
                    text="\n".join(buffer),
                )
            )
            chunk_index += 1
    return tuple(chunks)


def _split_oversized_segment(
    text: str,
    *,
    budget: int,
    cost: Callable[[str], int],
) -> tuple[str, ...]:
    if budget <= 0 or cost(text) <= budget:
        return (text,)

    pieces: list[str] = []
    for sentence in _sentence_pieces(text):
        if cost(sentence) <= budget:
            pieces.append(sentence)
        else:
            pieces.extend(_hard_split(sentence, budget=budget))

    merged: list[str] = []
    current = ""
    for piece in pieces:
        candidate = current + piece if current else piece
        if current and cost(candidate) > budget:
            merged.append(current.strip())
            current = piece
        else:
            current = candidate
    if current.strip():
        merged.append(current.strip())
    return tuple(piece for piece in merged if piece)


def _sentence_pieces(text: str) -> tuple[str, ...]:
    parts = [part for part in _SENTENCE_BOUNDARY_RE.split(text) if part]
    if not parts:
        return (text,)
    return tuple(parts)


def _hard_split(text: str, *, budget: int) -> tuple[str, ...]:
    if budget <= 0:
        return (text,)
    return tuple(text[index : index + budget] for index in range(0, len(text), budget))


__all__ = ["GlossaryChunk", "build_glossary_chunks", "clean_glossary_source_text"]
