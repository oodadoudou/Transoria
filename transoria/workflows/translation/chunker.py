"""Segment chunking and prompt-section formatting.

The chunker takes the per-document segment list, decides which segments go
into each LLM call, and composes the user-message sections (glossary +
context + JSONL input) that prepend the source lines for that call.

Indices in the JSONL stream are *chunk-local* (0-based) so the LLM operates
on a small contiguous window. The orchestrator re-attaches the chunk-local
index to a stable ``segment_id`` (``"<file_index>:<segment_index>"``) when
collecting results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from transoria.workflows.translation.preprocessor import PreprocessedSegment
from transoria.workflows.translation.rules import Glossary, GlossaryEntry


@dataclass(frozen=True)
class ChunkSegment:
    """One source line within a chunk, after preprocessing."""

    segment_id: str
    chunk_index: int
    prompt_text: str


@dataclass(frozen=True)
class TranslationChunk:
    """A batch of segments sent to the LLM in one call."""

    segments: tuple[ChunkSegment, ...]
    context_lines: tuple[str, ...] = ()
    glossary_entries: tuple[GlossaryEntry, ...] = ()

    def jsonl_input(self) -> str:
        return "\n".join(
            json.dumps(
                {str(segment.chunk_index): segment.prompt_text}, ensure_ascii=False
            )
            for segment in self.segments
        )


@dataclass(frozen=True)
class PreparedSegment:
    """Bridge type between orchestrator and chunker.

    Holds the segment id, the original (post-strip) source body, and the
    preprocessor output. The chunker only needs the prompt text + id, but the
    orchestrator keeps the original body around for context-window slicing
    and for the ``source_text`` written into the bilingual output.
    """

    segment_id: str
    original_text: str
    preprocessed: PreprocessedSegment


def build_chunks(
    prepared: Sequence[PreparedSegment],
    *,
    chunk_size: int,
    chunk_token_limit: int = 0,
    token_counter: Callable[[str], int] | None = None,
    context_line_count: int,
    glossary: Glossary,
) -> tuple[TranslationChunk, ...]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    use_tokens = token_counter is not None and chunk_token_limit > 0
    chunks: list[TranslationChunk] = []
    cursor = 0
    while cursor < len(prepared):
        window_end = _next_window_end(
            prepared,
            cursor,
            chunk_size=chunk_size,
            chunk_token_limit=chunk_token_limit,
            token_counter=token_counter,
            use_tokens=use_tokens,
        )
        window = prepared[cursor:window_end]
        segments = tuple(
            ChunkSegment(
                segment_id=item.segment_id,
                chunk_index=index,
                prompt_text=item.preprocessed.prompt_text,
            )
            for index, item in enumerate(window)
        )
        context = _context_window(prepared, cursor, context_line_count)
        matched = (
            glossary.match_many(item.preprocessed.prompt_text for item in window)
            if glossary.entries
            else ()
        )
        chunks.append(
            TranslationChunk(
                segments=segments,
                context_lines=context,
                glossary_entries=matched,
            )
        )
        cursor = window_end
    return tuple(chunks)


def _next_window_end(
    prepared: Sequence[PreparedSegment],
    cursor: int,
    *,
    chunk_size: int,
    chunk_token_limit: int,
    token_counter: Callable[[str], int] | None,
    use_tokens: bool,
) -> int:
    start_file = _file_key(prepared[cursor].segment_id)
    end = cursor
    token_cost = 0
    while end < len(prepared) and end - cursor < chunk_size:
        item = prepared[end]
        if _file_key(item.segment_id) != start_file:
            break
        if use_tokens:
            assert token_counter is not None
            additional = token_counter(item.preprocessed.prompt_text)
            if end > cursor and token_cost + additional > chunk_token_limit:
                break
            token_cost += additional
        end += 1
    return max(cursor + 1, end)


def _file_key(segment_id: str) -> str:
    return segment_id.split(":", 1)[0]


def _context_window(
    prepared: Sequence[PreparedSegment], cursor: int, count: int
) -> tuple[str, ...]:
    if count <= 0 or cursor == 0:
        return ()
    current_file = _file_key(prepared[cursor].segment_id)
    context: list[str] = []
    for index in range(cursor - 1, -1, -1):
        item = prepared[index]
        if _file_key(item.segment_id) != current_file:
            break
        context.append(item.original_text)
        if len(context) >= count:
            break
    return tuple(reversed(context))


def format_glossary_section(entries: Iterable[GlossaryEntry]) -> str:
    """Format matched glossary entries for inclusion in the user prompt."""

    lines: list[str] = []
    for entry in entries:
        if not entry.enabled or not entry.src:
            continue
        info = f" ({entry.info})" if entry.info else ""
        lines.append(f"- {entry.src} -> {entry.dst}{info}")
    return "\n".join(lines)


def format_context_section(context_lines: Iterable[str]) -> str:
    """Format preceding source lines as a small JSONL block.

    Negative indices distinguish context lines from the main translation
    block in the same envelope.
    """

    lines: list[str] = []
    materialized = list(context_lines)
    for offset, text in enumerate(materialized, start=1):
        index = offset - len(materialized) - 1  # ..., -3, -2, -1
        lines.append(json.dumps({str(index): text}, ensure_ascii=False))
    return "\n".join(lines)


def assemble_user_prompt(chunk: TranslationChunk) -> str:
    """Compose the user message: optional glossary, optional context,
    fenced JSONL block.

    The translate block is wrapped in a `````jsonline``
    code fence so the model sees an unambiguous format boundary; this is
    the strongest single signal we can give a chat model that "respond
    with the same shape between fences" — much more reliable than a
    plain label.
    """

    parts: list[str] = []
    glossary_section = format_glossary_section(chunk.glossary_entries)
    if glossary_section:
        parts.append("[Glossary]\n" + glossary_section)
    context_section = format_context_section(chunk.context_lines)
    if context_section:
        parts.append("[Context]\n" + context_section)
    parts.append(
        "[Translate]\n```jsonline\n" + chunk.jsonl_input() + "\n```"
    )
    return "\n\n".join(parts)


__all__ = [
    "ChunkSegment",
    "PreparedSegment",
    "TranslationChunk",
    "assemble_user_prompt",
    "build_chunks",
    "format_context_section",
    "format_glossary_section",
]
