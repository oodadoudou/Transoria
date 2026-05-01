"""Translation subtask runner: prompt → LLM → decode → postprocess.

The orchestrator builds chunks, serializes each into a subtask payload, and
hands the executor a runner instance. The executor calls
:meth:`TranslationSubtaskRunner.run` for every subtask. This file is the
narrowest place that talks to the LLM; everything above it deals in
preprocessed strings and translation results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from transoria.domain import Language
from transoria.llm.client import ChatRequest, LlmClient, LlmRequestError
from transoria.llm.config import ModelConfig
from transoria.llm.decoders import decode_translation_jsonl
from transoria.llm.retry import retry_async
from transoria.llm.usage import estimate_tokens_from_text
from transoria.runtime.key_pool import KeyPool
from transoria.runtime.rate_limit import TpmLimiter
from transoria.prompts import PromptContext, PromptPreset, build_prompt
from transoria.runtime.executor import SubtaskResult
from transoria.runtime.subtask import Subtask
from transoria.workflows.debug_log import write_subtask_debug_log
from transoria.workflows.fake_name import (
    FakeNameRoster,
    FakeNameSession,
    restore_fake_name_text,
)
from transoria.workflows.translation.chunker import (
    ChunkSegment,
    TranslationChunk,
    assemble_user_prompt,
)
from transoria.workflows.translation.confidence import (
    ConfidenceVerdict,
    evaluate_segment_confidence,
)
from transoria.workflows.translation.preprocessor import (
    ProtectionMap,
    postprocess_segment,
)
from transoria.workflows.translation.rules import (
    GlossaryEntry,
    ReplacementRule,
)


SUBTASK_PAYLOAD_VERSION = 1
SUBTASK_RESPONSE_VERSION = 2


# When the first attempt produces non-JSONL output (prose, mixed text,
# missing lines), prepend this banner to the user message on retry. The
# banner is intentionally stark: stating both what was wrong and the
# exact format expected gives the next sampling pass a much higher
# chance of complying than a silent retry.
_FORMAT_RETRY_REMINDER = (
    "FORMAT RETRY: the previous answer was rejected because it did not "
    "follow JSONLINE — too few lines, prose interleaved, or non-JSON text "
    "around the objects. Output JSONLINE only: one independent JSON object "
    "per line, exactly one line per source index. The first non-whitespace "
    'character must be "{". No prose, no Markdown headings, no code fence '
    "around the response, no extra lines."
)


@dataclass(frozen=True)
class _SegmentPayload:
    """Internal: per-segment data carried alongside the chunk in the payload."""

    segment_id: str
    chunk_index: int
    prompt_text: str
    original_text: str
    protection_spans: tuple[str, ...]
    leading_whitespace: str
    trailing_whitespace: str


def encode_subtask_payload(
    chunk: TranslationChunk,
    *,
    segment_metadata: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build a JSON-safe payload for a translation subtask.

    ``segment_metadata`` carries the per-segment data the chunk does not own
    (original text, protection spans, surrounding whitespace). It must be
    aligned with ``chunk.segments`` by ``chunk_index``.
    """

    if len(segment_metadata) != len(chunk.segments):
        raise ValueError(
            "segment_metadata length must match the chunk's segment count"
        )
    return {
        "version": SUBTASK_PAYLOAD_VERSION,
        "segments": [
            {
                "segment_id": segment.segment_id,
                "chunk_index": segment.chunk_index,
                "prompt_text": segment.prompt_text,
                "original_text": str(meta.get("original_text", "")),
                "protection_spans": list(meta.get("protection_spans", ())),
                "leading_whitespace": str(meta.get("leading_whitespace", "")),
                "trailing_whitespace": str(meta.get("trailing_whitespace", "")),
            }
            for segment, meta in zip(chunk.segments, segment_metadata)
        ],
        "context_lines": list(chunk.context_lines),
        "glossary_entries": [
            {
                "src": entry.src,
                "dst": entry.dst,
                "info": entry.info,
                "regex": entry.regex,
                "case_sensitive": entry.case_sensitive,
                "enabled": entry.enabled,
            }
            for entry in chunk.glossary_entries
        ],
    }


def _decode_subtask_payload(
    payload: Mapping[str, object],
) -> tuple[TranslationChunk, tuple[_SegmentPayload, ...]]:
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("Subtask payload has no segments")

    segments: list[ChunkSegment] = []
    metadata: list[_SegmentPayload] = []
    for entry in raw_segments:
        if not isinstance(entry, Mapping):
            raise ValueError(f"Invalid segment entry: {entry!r}")
        segment_id = str(entry["segment_id"])
        chunk_index = int(entry["chunk_index"])
        prompt_text = str(entry["prompt_text"])
        segments.append(
            ChunkSegment(
                segment_id=segment_id,
                chunk_index=chunk_index,
                prompt_text=prompt_text,
            )
        )
        spans = entry.get("protection_spans", ())
        if not isinstance(spans, (list, tuple)):
            spans = ()
        metadata.append(
            _SegmentPayload(
                segment_id=segment_id,
                chunk_index=chunk_index,
                prompt_text=prompt_text,
                original_text=str(entry.get("original_text", "")),
                protection_spans=tuple(str(span) for span in spans),
                leading_whitespace=str(entry.get("leading_whitespace", "")),
                trailing_whitespace=str(entry.get("trailing_whitespace", "")),
            )
        )

    raw_context = payload.get("context_lines", ())
    context_lines = tuple(str(line) for line in raw_context) if isinstance(
        raw_context, (list, tuple)
    ) else ()

    raw_glossary = payload.get("glossary_entries", ())
    glossary_entries: tuple[GlossaryEntry, ...] = ()
    if isinstance(raw_glossary, list):
        glossary_entries = tuple(
            GlossaryEntry(
                src=str(item.get("src", "")),
                dst=str(item.get("dst", "")),
                info=str(item.get("info", "")),
                regex=bool(item.get("regex", False)),
                case_sensitive=bool(item.get("case_sensitive", False)),
                enabled=bool(item.get("enabled", True)),
            )
            for item in raw_glossary
            if isinstance(item, Mapping)
        )

    chunk = TranslationChunk(
        segments=tuple(segments),
        context_lines=context_lines,
        glossary_entries=glossary_entries,
    )
    return chunk, tuple(metadata)


@dataclass(frozen=True)
class TranslationSubtaskRunner:
    """Runs one translation subtask end-to-end.

    Construction takes the immutable per-task settings (client, model,
    preset, languages, post-replacements, optional task-scoped key
    pool). Per-subtask data lives in the subtask payload that the
    orchestrator builds.
    """

    client: LlmClient
    model: ModelConfig
    prompt_preset: PromptPreset
    source_language: Language
    target_language: Language
    post_replacements: tuple[ReplacementRule, ...] = ()
    enable_confidence_check: bool = False
    min_length_ratio: float = 0.3
    max_length_ratio: float = 3.0
    max_punctuation_delta: int = 4
    low_confidence_max_retries: int = 0
    tpm_limiter: TpmLimiter | None = None
    key_pool: KeyPool | None = None
    stream: bool = False
    debug_log_dir: Path | None = None
    fake_name_roster: FakeNameRoster | FakeNameSession | None = None

    async def run(self, subtask: Subtask) -> SubtaskResult:
        chunk, metadata = _decode_subtask_payload(subtask.request_payload)
        attempt_index = -1

        async def operation() -> SubtaskResult:
            nonlocal attempt_index
            attempt_index += 1
            return await self._attempt(
                chunk, metadata, subtask.id, attempt_index=attempt_index
            )

        return await retry_async(operation, model=self.model)

    async def _attempt(
        self,
        chunk,
        metadata: tuple[_SegmentPayload, ...],
        subtask_id: str = "",
        *,
        attempt_index: int = 0,
    ) -> SubtaskResult:
        system_prompt = build_prompt(
            self.prompt_preset,
            PromptContext(
                source_language=self.source_language.value,
                target_language=self.target_language.value,
            ),
            thinking=self.model.thinking_prompt_enabled,
        )

        initial_user_prompt = self._compose_user_prompt(
            self._apply_roster(assemble_user_prompt(chunk)),
            format_retry=attempt_index > 0,
        )
        log_label = f"translation {subtask_id}" if subtask_id else "translation"
        response = await self._one_llm_call(
            system_prompt, initial_user_prompt, log_label
        )
        total_input = response.usage.input_tokens
        total_output = response.usage.output_tokens

        raw_content = self._restore_roster(response.content)
        translations_by_index = self._decode_or_raise(raw_content, metadata)

        finalized: dict[str, str] = {}
        low_confidence: list[dict[str, object]] = []
        pending: list[tuple[_SegmentPayload, str, tuple[str, ...]]] = []

        for meta in metadata:
            final_text = self._postprocess(meta, translations_by_index[meta.chunk_index])
            verdict = self._evaluate_confidence(meta.original_text, final_text)
            if verdict.is_low_confidence and self.low_confidence_max_retries > 0:
                pending.append((meta, final_text, verdict.reasons))
                continue
            finalized[meta.segment_id] = final_text
            if verdict.is_low_confidence:
                low_confidence.append(
                    {"segment_id": meta.segment_id, "reasons": list(verdict.reasons)}
                )

        retry_round = 0
        while pending and retry_round < self.low_confidence_max_retries:
            retry_round += 1
            retry_chunk = TranslationChunk(
                segments=tuple(
                    ChunkSegment(
                        segment_id=meta.segment_id,
                        chunk_index=meta.chunk_index,
                        prompt_text=meta.prompt_text,
                    )
                    for meta, _, _ in pending
                ),
                context_lines=chunk.context_lines,
                glossary_entries=chunk.glossary_entries,
            )
            retry_user_prompt = self._compose_user_prompt(
                self._apply_roster(assemble_user_prompt(retry_chunk)),
                format_retry=False,
            )
            retry_response = await self._one_llm_call(
                system_prompt, retry_user_prompt, f"{log_label} retry"
            )
            total_input += retry_response.usage.input_tokens
            total_output += retry_response.usage.output_tokens

            retry_raw = self._restore_roster(retry_response.content)
            retry_decoded = decode_translation_jsonl(retry_raw)
            retry_by_index = {line.index: line.text for line in retry_decoded.lines}

            next_pending: list[tuple[_SegmentPayload, str, tuple[str, ...]]] = []
            for meta, last_text, last_reasons in pending:
                retry_text = retry_by_index.get(meta.chunk_index)
                if retry_text is None:
                    next_pending.append((meta, last_text, last_reasons))
                    continue
                retry_final = self._postprocess(meta, retry_text)
                verdict = self._evaluate_confidence(meta.original_text, retry_final)
                if verdict.is_low_confidence:
                    next_pending.append((meta, retry_final, verdict.reasons))
                    continue
                finalized[meta.segment_id] = retry_final
            pending = next_pending

        for meta, last_text, last_reasons in pending:
            finalized[meta.segment_id] = last_text
            low_confidence.append(
                {
                    "segment_id": meta.segment_id,
                    "reasons": list(last_reasons) + ["force_accepted_after_max_retries"],
                }
            )

        payload: dict[str, object] = {
            "version": SUBTASK_RESPONSE_VERSION,
            "translations": finalized,
            "low_confidence": low_confidence,
        }
        if self.debug_log_dir is not None:
            write_subtask_debug_log(
                self.debug_log_dir,
                _chunk_log_id(chunk),
                {
                    "kind": "translation",
                    "system_prompt": system_prompt,
                    "user_prompt": initial_user_prompt,
                    "raw_response": raw_content,
                    "translations": finalized,
                    "low_confidence": low_confidence,
                    "retry_rounds": retry_round,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                        "total_tokens": total_input + total_output,
                    },
                },
            )
        return SubtaskResult(
            response_content=json.dumps(payload, ensure_ascii=False),
            input_tokens=total_input,
            output_tokens=total_output,
        )

    def _compose_user_prompt(
        self,
        body: str,
        *,
        format_retry: bool,
    ) -> str:
        parts: list[str] = []
        if format_retry:
            parts.append(_FORMAT_RETRY_REMINDER)
        parts.append(body)
        return "\n\n".join(part for part in parts if part)

    def _apply_roster(self, prompt: str) -> str:
        roster = self.fake_name_roster
        if roster is None or roster.is_empty():
            return prompt
        return roster.apply(prompt)

    def _restore_roster(self, content: str) -> str:
        roster = self.fake_name_roster
        if roster is None or roster.is_empty():
            return content
        return restore_fake_name_text(roster, content)

    async def _one_llm_call(
        self, system_prompt: str, user_prompt: str, log_label: str = ""
    ):
        reservation = -1
        if self.tpm_limiter is not None:
            estimated = estimate_tokens_from_text(
                system_prompt
            ) + estimate_tokens_from_text(user_prompt)
            reservation = await self.tpm_limiter.reserve(estimated)
        response = None
        try:
            response = await self.client.chat(
                ChatRequest(
                    model=self.model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    stream=self.stream,
                    key_pool=self.key_pool,
                    log_label=log_label,
                )
            )
        finally:
            if self.tpm_limiter is not None and reservation >= 0:
                actual = response.usage.total_tokens if response is not None else 0
                self.tpm_limiter.settle(reservation, actual)
        assert response is not None
        return response

    def _decode_or_raise(
        self, raw_content: str, metadata: tuple[_SegmentPayload, ...]
    ) -> dict[int, str]:
        decoded = decode_translation_jsonl(raw_content)
        translations_by_index = {line.index: line.text for line in decoded.lines}
        expected = {meta.chunk_index for meta in metadata}
        missing = expected - translations_by_index.keys()
        extra = translations_by_index.keys() - expected
        if missing or extra:
            issue_summary = ""
            if decoded.issues:
                issue_summary = "; decode_issues=" + " | ".join(
                    issue.reason for issue in decoded.issues
                )
            raise LlmRequestError(
                "Translation line count mismatch — "
                f"expected {sorted(expected)}, "
                f"got {sorted(translations_by_index.keys())}; "
                f"missing={sorted(missing)} extra={sorted(extra)}"
                + issue_summary,
                code="llm.line_count_mismatch",
            )
        return translations_by_index

    def _postprocess(self, meta: _SegmentPayload, translated: str) -> str:
        return postprocess_segment(
            translated,
            protection=ProtectionMap(spans=meta.protection_spans),
            leading_whitespace=meta.leading_whitespace,
            trailing_whitespace=meta.trailing_whitespace,
            post_replacements=self.post_replacements,
        )

    def _evaluate_confidence(
        self, source_text: str, translated_text: str
    ) -> ConfidenceVerdict:
        if not self.enable_confidence_check:
            return ConfidenceVerdict(is_low_confidence=False)
        return evaluate_segment_confidence(
            source_text,
            translated_text,
            min_length_ratio=self.min_length_ratio,
            max_length_ratio=self.max_length_ratio,
            max_punctuation_delta=self.max_punctuation_delta,
            source_language=self.source_language,
        )


def _chunk_log_id(chunk) -> str:  # noqa: ANN001 — accepts TranslationChunk
    if not chunk.segments:
        return "chunk"
    return f"chunk-{chunk.segments[0].segment_id.replace(':', '_')}"


__all__ = [
    "SUBTASK_PAYLOAD_VERSION",
    "TranslationSubtaskRunner",
    "encode_subtask_payload",
]
