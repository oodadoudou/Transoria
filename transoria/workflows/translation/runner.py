"""Translation subtask runner: prompt → LLM → decode → postprocess.

The orchestrator builds chunks, serializes each into a subtask payload, and
hands the executor a runner instance. The executor calls
:meth:`TranslationSubtaskRunner.run` for every subtask. This file is the
narrowest place that talks to the LLM; everything above it deals in
preprocessed strings and translation results.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from transoria.domain import Language, language_prompt_label, normalize_target_script
from transoria.llm.client import ChatRequest, LlmClient, LlmRequestError
from transoria.llm.config import ModelConfig
from transoria.llm.decoders import decode_translation_jsonl
from transoria.llm.retry import is_transient_llm_error, retry_async
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
_SOURCE_FALLBACK_RESIDUE_RATIO = 0.15
_RESCUE_TRANSPORT_RETRY_BUDGET = 1
_TRANSLATION_TRANSPORT_RETRY_BUDGET = 1
_HIGH_CONCURRENCY_THRESHOLD = 20
_HIGH_CONCURRENCY_BATCH_TIMEOUT_SECONDS = 360.0
_HIGH_CONCURRENCY_RESCUE_TIMEOUT_SECONDS = 60.0


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

# Custom presets that say nothing about the wire format need a
# system-side reminder so a long literary persona can't drown out the
# user-message format contract. Mentions only the transport (JSONLINE
# shape, line count), never style/voice/extraction policy — runtime
# protocol is allowed to be enforced from runtime; user-authored
# extraction or style preferences must continue to live entirely in the
# preset.
_SYSTEM_FORMAT_CONTRACT_HINT = (
    "\n\n[Output transport — runtime protocol]\n"
    "Respond as JSONLINE: one JSON object per source index, exactly "
    'matching {"<INDEX>":"<translated text>"}. Return every index '
    "exactly once and only those indices. Do not wrap the response in "
    "Markdown, prose, headings, or a code fence."
)
_SYSTEM_LANGUAGE_CONTRACT_HINT = (
    "\n\n[Output language — runtime selection]\n"
    "Translate every translatable part into {target_language}. Do not output "
    "another target language or script unless the source contains a name, ID, "
    "code fragment, URL, file path, or other non-translatable literal that "
    "must be preserved verbatim."
)


_JSONL_KEYWORD_PATTERN = re.compile(
    r"jsonl(?:ine)?|\{\s*\"\s*<\s*INDEX", re.IGNORECASE
)


def _augment_system_prompt(
    system_prompt: str, preset: PromptPreset, *, target_language: str
) -> str:
    """Add runtime language guardrails and missing JSONL transport rules."""

    parts = [
        system_prompt,
        _SYSTEM_LANGUAGE_CONTRACT_HINT.format(target_language=target_language),
    ]
    if not getattr(preset, "is_system", False) and not _JSONL_KEYWORD_PATTERN.search(
        system_prompt
    ):
        parts.append(_SYSTEM_FORMAT_CONTRACT_HINT)
    return "".join(parts)


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


def _with_translation_request_timeout(model: ModelConfig, phase: str) -> ModelConfig:
    if model.concurrency_limit <= _HIGH_CONCURRENCY_THRESHOLD:
        return model
    timeout = (
        _HIGH_CONCURRENCY_BATCH_TIMEOUT_SECONDS
        if phase == "batch"
        else _HIGH_CONCURRENCY_RESCUE_TIMEOUT_SECONDS
    )
    if model.timeout_seconds <= timeout:
        return model
    return replace(model, timeout_seconds=timeout)


def _transport_retry_budget(_model: ModelConfig) -> int:
    return _TRANSLATION_TRANSPORT_RETRY_BUDGET


def _should_retry_translation_error(model: ModelConfig, exc: BaseException) -> bool:
    if model.concurrency_limit <= _HIGH_CONCURRENCY_THRESHOLD:
        return is_transient_llm_error(exc)
    if (
        isinstance(exc, LlmRequestError)
        and getattr(exc, "code", "") == "llm.transport_error"
    ):
        message = str(exc).lower()
        if "timeout" in message:
            return False
    return is_transient_llm_error(exc)


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
    min_length_ratio: float = 0.25
    max_length_ratio: float = 4.0
    max_punctuation_delta: int = 12
    low_confidence_max_retries: int = 0
    tpm_limiter: TpmLimiter | None = None
    key_pool: KeyPool | None = None
    stream: bool = False
    debug_log_dir: Path | None = None
    fake_name_roster: FakeNameRoster | FakeNameSession | None = None
    solo_retry_limiter: asyncio.Semaphore | None = None

    async def run(self, subtask: Subtask) -> SubtaskResult:
        chunk, metadata = _decode_subtask_payload(subtask.request_payload)
        return await self._attempt(chunk, metadata, subtask.id)

    async def _attempt(
        self,
        chunk: TranslationChunk,
        metadata: tuple[_SegmentPayload, ...],
        subtask_id: str = "",
    ) -> SubtaskResult:
        system_prompt = _augment_system_prompt(
            build_prompt(
                self.prompt_preset,
                PromptContext(
                    source_language=language_prompt_label(self.source_language),
                    target_language=language_prompt_label(self.target_language),
                ),
                thinking=self.model.thinking_prompt_enabled,
            ),
            self.prompt_preset,
            target_language=language_prompt_label(self.target_language),
        )
        log_label = f"translation {subtask_id}" if subtask_id else "translation"

        accumulated: dict[int, str] = {}
        solo_retried_indices: set[int] = set()
        fallback_reasons_by_index: dict[int, str] = {}
        debug_attempts: list[dict[str, object]] = []
        metadata_by_index = {m.chunk_index: m for m in metadata}
        pending_indices: set[int] = set(metadata_by_index.keys())
        retries_remaining = max(0, self.model.retry_attempts)
        total_input = 0
        total_output = 0
        last_raw = ""
        first_user_prompt = ""
        finalized: dict[str, str] = {}
        low_confidence: list[dict[str, object]] = []
        retry_round = 0
        terminal_error: BaseException | None = None

        try:
            while True:
                pending_meta = tuple(
                    metadata_by_index[i] for i in sorted(pending_indices)
                )
                sub_chunk = _build_subchunk_from_pending(
                    chunk,
                    pending_meta,
                    include_context=False,
                )
                user_prompt = self._compose_user_prompt(
                    self._apply_roster(assemble_user_prompt(sub_chunk)),
                    format_retry=len(debug_attempts) > 0,
                )
                if not first_user_prompt:
                    first_user_prompt = user_prompt

                phase = "batch" if not debug_attempts else "partial_retry"
                request_model = _with_translation_request_timeout(self.model, phase)

                async def _llm_call() -> object:
                    return await self._one_llm_call(
                        system_prompt,
                        user_prompt,
                        log_label,
                        model=request_model,
                    )

                try:
                    response = await asyncio.wait_for(
                        retry_async(
                            _llm_call,
                            model=request_model,
                            max_retry_attempts=(
                                None
                                if phase == "batch"
                                else _RESCUE_TRANSPORT_RETRY_BUDGET
                            ),
                            max_transport_retry_attempts=_transport_retry_budget(
                                self.model
                            ),
                            should_retry=lambda exc: _should_retry_translation_error(
                                self.model, exc
                            ),
                        ),
                        timeout=request_model.timeout_seconds,
                    )
                except BaseException as exc:
                    if phase == "batch" or not is_transient_llm_error(exc):
                        debug_attempts.append(
                            {
                                "phase": phase,
                                "user_prompt": user_prompt,
                                "timeout_seconds": request_model.timeout_seconds,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        raise
                    for idx in sorted(pending_indices):
                        if idx in metadata_by_index:
                            fallback_reasons_by_index[idx] = (
                                "partial_retry_transient_failed"
                            )
                    debug_attempts.append(
                        {
                            "phase": phase,
                            "user_prompt": user_prompt,
                            "timeout_seconds": request_model.timeout_seconds,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    pending_indices.clear()
                    break
                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens
                raw_content = self._restore_roster(response.content)
                last_raw = raw_content
                debug_attempts.append(
                    {
                        "phase": phase,
                        "user_prompt": user_prompt,
                        "timeout_seconds": request_model.timeout_seconds,
                        "raw_response": raw_content,
                    }
                )

                translations, missing = self._decode_partial(
                    raw_content, pending_meta, sub_chunk.context_lines
                )
                for idx, text in translations.items():
                    accumulated[idx] = text
                pending_indices = set(missing)

                if not pending_indices:
                    # All asked-for indices present. Check duplicate drift
                    # across the full accumulated set; if found, re-pend the
                    # suspicious indices and ask the model again with a
                    # narrow sub-chunk. The model with less context often
                    # produces distinct translations on the retry, avoiding
                    # the orchestrator-level chunk split which is much
                    # more expensive.
                    suspicious = _detect_duplicate_drift(accumulated, metadata)
                    if not suspicious:
                        break
                    for idx in suspicious:
                        accumulated.pop(idx, None)
                    pending_indices = set(suspicious)

                if retries_remaining <= 0:
                    fallback_reason = (
                        "duplicate_drift_after_max_retries"
                        if all(
                            idx in metadata_by_index
                            for idx in pending_indices
                        )
                        and not missing
                        else "line_count_mismatch_after_max_retries"
                    )
                    for idx in sorted(pending_indices):
                        meta = metadata_by_index.get(idx)
                        if meta is None:
                            continue
                        accumulated[idx] = meta.prompt_text
                        fallback_reasons_by_index[idx] = fallback_reason
                    pending_indices.clear()
                    break
                retries_remaining -= 1

            pending: list[tuple[_SegmentPayload, str, tuple[str, ...]]] = []

            for meta in metadata:
                fallback_reason = fallback_reasons_by_index.get(
                    meta.chunk_index
                )
                if fallback_reason:
                    final_text = meta.original_text
                else:
                    final_text = self._postprocess(
                        meta, accumulated[meta.chunk_index]
                    )
                verdict = self._evaluate_confidence(meta.original_text, final_text)
                extra_reasons: list[str] = []
                if fallback_reason:
                    extra_reasons.append(fallback_reason)
                if (
                    (verdict.is_low_confidence or fallback_reason)
                    and self.low_confidence_max_retries > 0
                ):
                    pending.append(
                        (meta, final_text, tuple(extra_reasons) + verdict.reasons)
                    )
                    continue
                finalized[meta.segment_id] = final_text
                if verdict.is_low_confidence or extra_reasons:
                    entry: dict[str, object] = {
                        "segment_id": meta.segment_id,
                        "reasons": extra_reasons + list(verdict.reasons),
                    }
                    tags: list[str] = []
                    if fallback_reason or any(
                        "residue" in str(r).lower()
                        for r in verdict.reasons
                    ):
                        tags.append("source_residue")
                    if tags:
                        entry["tags"] = tags
                    low_confidence.append(entry)

            # Single-item retry: each pending segment gets up to
            # ``low_confidence_max_retries`` solo LLM calls, in isolation.
            # When the model sees only one source line per request its
            # attention isn't diluted across other segments of the chunk,
            # so short fillers (네./응./等) and ambiguous phrases that
            # were echoed in the batch call usually get a real translation.
            still_pending: list[tuple[_SegmentPayload, str, tuple[str, ...]]] = []
            for meta, last_text, last_reasons in pending:
                solo_retried_indices.add(meta.chunk_index)
                current_text = last_text
                current_reasons = last_reasons
                for solo_round in range(self.low_confidence_max_retries):
                    retry_round = max(retry_round, solo_round + 1)
                    # Mirror the proofreading-page "retranslate" path
                    # exactly: chunk_index=0 (model sees an isolated
                    # single-line task, not "line N of some chunk"),
                    # no context_lines (no neighboring segments to
                    # compete for attention), glossary entries matched
                    # against just this one source. This eliminates the
                    # batch-context drift where the model keyed a
                    # neighbor's translation under the wrong index.
                    solo_chunk = TranslationChunk(
                        segments=(
                            ChunkSegment(
                                segment_id=meta.segment_id,
                                chunk_index=0,
                                prompt_text=meta.prompt_text,
                            ),
                        ),
                        context_lines=(),
                        glossary_entries=chunk.glossary_entries,
                    )
                    solo_user_prompt = self._compose_user_prompt(
                        self._apply_roster(assemble_user_prompt(solo_chunk)),
                        format_retry=False,
                    )
                    solo_request_model = _with_translation_request_timeout(
                        self.model, "solo_retry"
                    )

                    async def _solo_llm_call() -> object:
                        return await self._one_llm_call(
                            system_prompt,
                            solo_user_prompt,
                            f"{log_label} solo-retry {meta.segment_id}",
                            model=solo_request_model,
                        )

                    try:
                        if self.solo_retry_limiter is None:
                            solo_response = await asyncio.wait_for(
                                retry_async(
                                    _solo_llm_call,
                                    model=solo_request_model,
                                    max_retry_attempts=_RESCUE_TRANSPORT_RETRY_BUDGET,
                                    max_transport_retry_attempts=_transport_retry_budget(
                                        self.model
                                    ),
                                    should_retry=lambda exc: _should_retry_translation_error(
                                        self.model, exc
                                    ),
                                ),
                                timeout=solo_request_model.timeout_seconds,
                            )
                        else:
                            async with self.solo_retry_limiter:
                                solo_response = await asyncio.wait_for(
                                    retry_async(
                                        _solo_llm_call,
                                        model=solo_request_model,
                                        max_retry_attempts=_RESCUE_TRANSPORT_RETRY_BUDGET,
                                        max_transport_retry_attempts=_transport_retry_budget(
                                            self.model
                                        ),
                                        should_retry=lambda exc: _should_retry_translation_error(
                                            self.model, exc
                                        ),
                                    ),
                                    timeout=solo_request_model.timeout_seconds,
                                )
                    except (LlmRequestError, TimeoutError) as exc:
                        if not is_transient_llm_error(exc):
                            raise
                        current_reasons = tuple(current_reasons) + (
                            "low_confidence_retry_transient_failed",
                        )
                        debug_attempts.append(
                            {
                                "phase": "low_confidence_solo_retry",
                                "user_prompt": solo_user_prompt,
                                "segment_id": meta.segment_id,
                                "timeout_seconds": solo_request_model.timeout_seconds,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        break
                    total_input += solo_response.usage.input_tokens
                    total_output += solo_response.usage.output_tokens
                    solo_raw = self._restore_roster(solo_response.content)
                    debug_attempts.append(
                        {
                            "phase": "low_confidence_solo_retry",
                            "user_prompt": solo_user_prompt,
                            "raw_response": solo_raw,
                            "segment_id": meta.segment_id,
                            "timeout_seconds": solo_request_model.timeout_seconds,
                        }
                    )
                    decoded = decode_translation_jsonl(solo_raw)
                    # Single-segment response: take the first parsed
                    # value regardless of its JSON key. Models
                    # occasionally emit a wrong key (e.g. echoing the
                    # original chunk_index even when we asked with
                    # chunk_index=0); positional acceptance keeps the
                    # content from landing under the wrong segment.
                    retry_text = (
                        decoded.lines[0].text if decoded.lines else None
                    )
                    if retry_text is None:
                        continue
                    retry_final = self._postprocess(meta, retry_text)
                    verdict = self._evaluate_confidence(
                        meta.original_text, retry_final
                    )
                    if not verdict.is_low_confidence:
                        finalized[meta.segment_id] = retry_final
                        current_text = None  # signal: passed
                        break
                    if retry_final.strip() == current_text.strip():
                        current_reasons = verdict.reasons
                        break
                    # Still low-conf: keep tracking the current best as
                    # whatever has the *least* source-language residue.
                    # An attempt that's mostly Chinese is preferable to a
                    # source-language echo even if both are low-conf.
                    if _residue_score(retry_final, self.source_language) < (
                        _residue_score(current_text, self.source_language)
                    ):
                        current_text = retry_final
                        current_reasons = verdict.reasons
                if current_text is not None:
                    still_pending.append((meta, current_text, current_reasons))
            pending = still_pending

            for meta, last_text, last_reasons in pending:
                has_residue = any(
                    "residue" in str(r).lower() for r in last_reasons
                )
                echoes_source = (
                    last_text.strip() == meta.original_text.strip()
                )
                residue_ratio = _residue_score(last_text, self.source_language)
                # Three terminal modes:
                # - Model echoed source: source-passthrough (same effect,
                #   clearer reason for the user).
                # - Model's last attempt is mostly source-language residue:
                #   source-passthrough. A mostly translated sentence with a
                #   small residue leak is kept and tagged; it is easier to
                #   fix than the raw source line.
                # - Model produced a Chinese guess that's flawed but not
                #   residue: keep it. A questionable Chinese line is
                #   easier to fix than re-translating from scratch.
                tags: list[str] = []
                if has_residue or echoes_source:
                    tags.append("source_residue")
                if (
                    echoes_source
                    or (has_residue and residue_ratio >= _SOURCE_FALLBACK_RESIDUE_RATIO)
                ):
                    finalized[meta.segment_id] = meta.original_text
                    extra_reason = "fell_back_to_source_after_max_retries"
                else:
                    finalized[meta.segment_id] = last_text
                    extra_reason = "force_accepted_after_max_retries"
                entry: dict[str, object] = {
                    "segment_id": meta.segment_id,
                    "reasons": list(last_reasons) + [extra_reason],
                }
                if tags:
                    entry["tags"] = tags
                low_confidence.append(entry)

            finalized_by_index = {
                meta.chunk_index: finalized[meta.segment_id]
                for meta in metadata
                if meta.segment_id in finalized
            }
            post_retry_drift = set(
                _detect_duplicate_drift(finalized_by_index, metadata)
            )
            if post_retry_drift:
                unresolved = post_retry_drift - solo_retried_indices
                if not unresolved:
                    unresolved = post_retry_drift
                for meta in metadata:
                    if meta.chunk_index not in unresolved:
                        continue
                    current_text = finalized.get(meta.segment_id, "")
                    tags = ["possible_duplicate"]
                    if not current_text or (
                        current_text.strip() == meta.original_text.strip()
                    ):
                        finalized[meta.segment_id] = meta.original_text
                        tags = ["source_residue"]
                    else:
                        verdict = self._evaluate_confidence(
                            meta.original_text, current_text
                        )
                        if any(
                            "residue" in str(reason).lower()
                            for reason in verdict.reasons
                        ):
                            tags.append("source_residue")
                    low_confidence.append(
                        {
                            "segment_id": meta.segment_id,
                            "reasons": [
                                "duplicate_drift_after_low_confidence_retry"
                            ],
                            "tags": tags,
                        }
                    )

            payload: dict[str, object] = {
                "version": SUBTASK_RESPONSE_VERSION,
                "translations": finalized,
                "low_confidence": low_confidence,
            }
            return SubtaskResult(
                response_content=json.dumps(payload, ensure_ascii=False),
                input_tokens=total_input,
                output_tokens=total_output,
            )
        except BaseException as exc:
            terminal_error = exc
            raise
        finally:
            if self.debug_log_dir is not None:
                # subtask_id is unique per split child; _chunk_log_id from
                # first segment_id collides between parent and child chunks.
                # The finally clause means failures (including raises mid-
                # loop or during low-conf retry) still preserve a debug log,
                # so the cache snapshot a user ships always carries the
                # full LLM-call trace even when the chunk ended in raise.
                log_payload: dict[str, object] = {
                    "kind": "translation",
                    "system_prompt": system_prompt,
                    "user_prompt": first_user_prompt,
                    "raw_response": last_raw,
                    "translations": finalized,
                    "low_confidence": low_confidence,
                    "attempts": debug_attempts,
                    "retry_rounds": retry_round,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                        "total_tokens": total_input + total_output,
                    },
                }
                if terminal_error is not None:
                    log_payload["terminal_error"] = (
                        f"{type(terminal_error).__name__}: {terminal_error}"
                    )
                write_subtask_debug_log(
                    self.debug_log_dir,
                    subtask_id or _chunk_log_id(chunk),
                    log_payload,
                )

    def _compose_user_prompt(
        self,
        body: str,
        *,
        format_retry: bool,
    ) -> str:
        # Format contract lives in the system prompt (built-in suffix or
        # ``_augment_system_prompt``'s injected hint) so we don't repeat
        # it in every user message — that overhead was 3-5x amplified
        # by small chunk sizes. The retry banner still prepends here on
        # the second-and-later attempts.
        if format_retry:
            return f"{_FORMAT_RETRY_REMINDER}\n\n{body}"
        return body

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
        self,
        system_prompt: str,
        user_prompt: str,
        log_label: str = "",
        *,
        model: ModelConfig | None = None,
    ):
        request_model = model or self.model
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
                    model=request_model,
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

    def _decode_partial(
        self,
        raw_content: str,
        metadata: Sequence[_SegmentPayload],
        context_lines: Sequence[str] = (),
    ) -> tuple[dict[int, str], frozenset[int]]:
        """Decode the LLM response into ``{chunk_index: text}`` and the
        set of expected indices that did not appear.

        Two decode paths, picked by line-count:

        1. **Key-complete** — when every requested ``chunk_index`` is
           present exactly once, trust the keys even if the model
           reordered the JSONL rows. Correct keys are stronger alignment
           evidence than response order.

        2. **Positional** — when the response has exactly one parsed
           line per requested segment and none of the emitted keys match
           the request, zip by source position. This recovers from stale
           labels / JSON arrays while avoiding mixed-key guesses.

        3. **Key-based fallback** — when the line count differs, the
           response is partial / has extras, so we have to trust keys.
           Lines whose keys are outside the requested set are dropped
           silently; happens when a partial-retry call asks for a
           subset and the model echoes the full prior chunk.

        Duplicate-drift detection runs on the accumulated dict in the
        caller, not here.
        """

        decoded = decode_translation_jsonl(raw_content)
        expected = {meta.chunk_index for meta in metadata}
        decoded_indices = {line.index for line in decoded.lines}
        if (
            len(decoded.lines) == 1
            and len(metadata) > 1
            and _all_sources_equivalent(metadata)
        ):
            text = decoded.lines[0].text
            return {meta.chunk_index: text for meta in metadata}, frozenset()
        sorted_expected = sorted(expected)
        decoded_order = [line.index for line in decoded.lines]
        if (
            context_lines
            and decoded.lines
            and len(decoded.lines) < len(expected)
            and len(decoded.lines) <= len(context_lines)
            and decoded_order == sorted_expected[: len(decoded_order)]
        ):
            return {}, frozenset(expected)
        if (
            len(metadata) >= _DENSE_PREFIX_PARTIAL_MIN_SEGMENTS
            and decoded.lines
            and len(decoded.lines) < len(expected)
            and decoded_order == sorted_expected[: len(decoded_order)]
            and not _all_sources_equivalent(metadata)
        ):
            return {}, frozenset(expected)
        if context_lines and len(decoded.lines) > len(expected):
            return {}, frozenset(expected)
        if decoded_indices == expected and len(decoded.lines) == len(expected):
            translations_by_index = {
                line.index: line.text
                for line in decoded.lines
            }
        elif (
            len(decoded.lines) == len(metadata)
            and metadata
            and decoded_indices.isdisjoint(expected)
        ):
            sorted_meta = sorted(metadata, key=lambda m: m.chunk_index)
            translations_by_index = {
                meta.chunk_index: line.text
                for meta, line in zip(sorted_meta, decoded.lines)
            }
        else:
            translations_by_index = {
                line.index: line.text
                for line in decoded.lines
                if line.index in expected
            }
        missing = frozenset(expected - translations_by_index.keys())
        return translations_by_index, missing

    def _postprocess(self, meta: _SegmentPayload, translated: str) -> str:
        processed = postprocess_segment(
            translated,
            protection=ProtectionMap(spans=meta.protection_spans),
            leading_whitespace=meta.leading_whitespace,
            trailing_whitespace=meta.trailing_whitespace,
            post_replacements=self.post_replacements,
        )
        return normalize_target_script(processed, self.target_language)

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
            target_language=self.target_language,
        )


def _chunk_log_id(chunk) -> str:  # noqa: ANN001 — accepts TranslationChunk
    if not chunk.segments:
        return "chunk"
    return f"chunk-{chunk.segments[0].segment_id.replace(':', '_')}"


_DENSE_PREFIX_PARTIAL_MIN_SEGMENTS = 8
_DUPLICATE_DRIFT_MIN_TEXT_LENGTH = 10
_NEAR_DUPLICATE_DRIFT_MEDIUM_TEXT_LENGTH = 12
_NEAR_DUPLICATE_MEDIUM_TRANSLATION_RATIO = 0.92
_NEAR_DUPLICATE_MEDIUM_SOURCE_RATIO = 0.45
_NEAR_DUPLICATE_DRIFT_MIN_TEXT_LENGTH = 40
_NEAR_DUPLICATE_TRANSLATION_RATIO = 0.70
_NEAR_DUPLICATE_SOURCE_RATIO = 0.55
_TEXT_SIMILARITY_NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _detect_duplicate_drift(
    translations_by_index: dict[int, str],
    metadata: tuple["_SegmentPayload", ...],
) -> list[int]:
    """Flag indices that look like model-laziness duplicate output.

    Short translations legitimately collide across distinct sources —
    affirmatives (응/어/네 → 嗯。), domain idioms (체크/체크메이트 → 将军。 in
    chess), titles, exclamations — so we require either translation
    length ≥ 10 chars OR three+ distinct sources sharing one output
    before treating it as drift. Real "model copy-pastes one sentence
    across the chunk" failures produce many cells holding a full-length
    sentence; short-text coincidences are domain vocabulary, not drift.
    """

    by_idx = {m.chunk_index: m for m in metadata}
    by_text: dict[str, list[int]] = {}
    for idx, text in translations_by_index.items():
        norm = text.strip()
        if not norm:
            continue
        by_text.setdefault(norm, []).append(idx)

    suspicious: set[int] = set()
    for text, indices in by_text.items():
        if len(indices) <= 1:
            continue
        sources = {
            by_idx[i].original_text.strip() for i in indices if i in by_idx
        }
        if len(sources) <= 1:
            continue
        if _all_texts_equivalent(sources):
            continue
        if len(text) < _DUPLICATE_DRIFT_MIN_TEXT_LENGTH and len(sources) < 3:
            continue
        suspicious.update(indices)
    by_idx = {m.chunk_index: m for m in metadata}
    items = [
        (idx, text.strip())
        for idx, text in translations_by_index.items()
        if len(text.strip()) >= _NEAR_DUPLICATE_DRIFT_MEDIUM_TEXT_LENGTH
    ]
    items.sort(key=lambda item: item[0])
    for (left_idx, left_text), (right_idx, right_text) in zip(items, items[1:]):
        left_meta = by_idx.get(left_idx)
        right_meta = by_idx.get(right_idx)
        if left_meta is None or right_meta is None:
            continue
        translation_similarity = _text_similarity(left_text, right_text)
        if len(left_text) >= _NEAR_DUPLICATE_DRIFT_MIN_TEXT_LENGTH:
            translation_threshold = _NEAR_DUPLICATE_TRANSLATION_RATIO
            source_threshold = _NEAR_DUPLICATE_SOURCE_RATIO
        else:
            translation_threshold = _NEAR_DUPLICATE_MEDIUM_TRANSLATION_RATIO
            source_threshold = _NEAR_DUPLICATE_MEDIUM_SOURCE_RATIO
        if translation_similarity < translation_threshold:
            continue
        if (
            _text_similarity(left_meta.original_text, right_meta.original_text)
            >= source_threshold
        ):
            continue
        suspicious.update((left_idx, right_idx))
    return sorted(suspicious)


def _text_similarity(left: str, right: str) -> float:
    left_norm = _normalize_for_similarity(left)
    right_norm = _normalize_for_similarity(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(
        None, left_norm, right_norm, autojunk=False
    ).ratio()


def _normalize_for_similarity(text: str) -> str:
    return _TEXT_SIMILARITY_NORMALIZE_RE.sub("", text)


def _all_sources_equivalent(metadata: Sequence["_SegmentPayload"]) -> bool:
    return _all_texts_equivalent(meta.original_text for meta in metadata)


def _all_texts_equivalent(texts: Iterable[str]) -> bool:
    normalized = {
        _normalize_for_similarity(text)
        for text in texts
        if _normalize_for_similarity(text)
    }
    return len(normalized) == 1


_KOREAN_RESIDUE_RE = re.compile(
    r"[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-힯ힰ-퟿ﾠ-ￜ]"
)
_JAPANESE_RESIDUE_RE = re.compile(
    "[぀-゚ゝ-ゟ゠-ヺヽ-ヿㇰ-ㇿｦ-ﾟ]"
)


def _residue_score(text: str, source_language: Language) -> float:
    """Fraction of source-language characters in ``text``. Lower = better
    (more translation effort, less residue). Used by the single-item
    retry loop to keep the cleanest candidate seen so far."""

    if not text:
        return 1.0
    if source_language is Language.KOREAN:
        pattern = _KOREAN_RESIDUE_RE
    elif source_language is Language.JAPANESE:
        pattern = _JAPANESE_RESIDUE_RE
    else:
        return 0.0
    return len(pattern.findall(text)) / max(1, len(text))


def _build_subchunk_from_pending(
    original: TranslationChunk,
    pending: Sequence[_SegmentPayload],
    *,
    include_context: bool = True,
) -> TranslationChunk:
    pending_indices = {m.chunk_index for m in pending}
    return TranslationChunk(
        segments=tuple(
            seg for seg in original.segments if seg.chunk_index in pending_indices
        ),
        context_lines=original.context_lines if include_context else (),
        glossary_entries=original.glossary_entries,
    )


__all__ = [
    "SUBTASK_PAYLOAD_VERSION",
    "TranslationSubtaskRunner",
    "encode_subtask_payload",
]
