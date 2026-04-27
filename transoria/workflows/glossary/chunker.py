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
            join_cost = 1 if buffer else 0
            segment_cost = cost(segment)
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
            buffer.append(segment)
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


__all__ = ["GlossaryChunk", "build_glossary_chunks", "clean_glossary_source_text"]
