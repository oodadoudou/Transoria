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
import unicodedata
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
from transoria.runtime.request_log import append_local_failure
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
    Glossary,
    GlossaryEntry,
    ReplacementRule,
)


SUBTASK_PAYLOAD_VERSION = 1
SUBTASK_RESPONSE_VERSION = 2
_SOURCE_FALLBACK_RESIDUE_RATIO = 0.15
_RESCUE_TRANSPORT_RETRY_BUDGET = 1
# Hard cap on transport-level retries (timeout / 429 / 5xx / read error) for the
# batch call. Transport retries are cheap (a failed connection or 5xx produces
# no output tokens) and transient outages — read timeouts, 503 "model loading" —
# routinely need a few attempts to clear, so allow 3 (4 attempts) while still
# capping below the user's configured network-retry budget so a wedged provider
# can't burn it all on one chunk.
_TRANSLATION_TRANSPORT_RETRY_BUDGET = 3
# Bounded re-asks for missing/duplicate lines within one subtask. This is a
# content-quality loop (only the still-missing indices are re-sent, so it is
# cheap), kept small because a model that mis-aligns twice rarely self-heals on
# a third batch ask — the leftover lines fall through to the isolated solo
# retry instead. Deliberately decoupled from the user's network-retry setting.
_PARTIAL_ACCEPT_MAX_RETRIES = 2
# A chunk with many safety refusals or source echoes can mark most lines as
# low-confidence. Keep rescue useful for normal cases, but bound the total paid
# re-asks so one pathological chunk cannot hold the task for minutes.
_LOW_CONFIDENCE_RETRY_CALLS_PER_CONFIGURED_RETRY = 4
_LOW_CONFIDENCE_RETRY_ATTEMPT_BUDGET = 1
_LOW_CONFIDENCE_MICRO_BATCH_MIN_SEGMENTS = 4
_LOW_CONFIDENCE_MICRO_BATCH_MAX_SEGMENTS = 5
_LOW_CONFIDENCE_MICRO_BATCH_TOKEN_CAP = 1200
_MASS_SOURCE_RESIDUE_MIN_SEGMENTS = 4
_MASS_SOURCE_RESIDUE_RATIO = 0.5
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
_SOURCE_RESIDUE_RETRY_REMINDER = (
    "SOURCE-RESIDUE RETRY: the previous answer was rejected because it copied "
    "source text or kept source-language residue. Translate every requested "
    "line fully into {target_language}. Output JSONLINE only: exactly one "
    "object per requested source index, with no prose or extra lines."
)
_LOW_CONFIDENCE_RETRY_REMINDER = (
    "QUALITY RETRY: the previous answer was rejected by quality checks. "
    "Translate every requested line fully into {target_language}. Output "
    "JSONLINE only: exactly one object per requested source index, with no "
    "prose or extra lines."
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


def _looks_truncated_jsonl_response(raw_content: str) -> bool:
    """Return True when the last emitted JSONL row is visibly incomplete.

    Some OpenAI-compatible providers stream a valid dense prefix and then cut
    the response mid-object. ``json_repair`` can salvage that partial final row
    into a misleading short value, so callers should keep only the fully closed
    prefix rows and retry the tail.
    """

    lines = [line.strip().rstrip(",") for line in raw_content.splitlines()]
    lines = [line for line in lines if line and not line.startswith("```")]
    if not lines:
        return False
    return not lines[-1].endswith("}")


_JSONL_KEYWORD_PATTERN = re.compile(
    r"jsonl(?:ine)?|\{\s*\"\s*<\s*INDEX", re.IGNORECASE
)
_LOW_CONFIDENCE_RETRY_EQUIVALENT_PATTERN = re.compile(
    r"[\s,，.。;；:：!！?？\"'“”‘’「」『』《》〈〉()\[\]{}（）【】<>〈〉\-—–_]+"
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


def _same_low_confidence_retry_candidate(left: str, right: str) -> bool:
    return _normalize_low_confidence_retry_candidate(
        left
    ) == _normalize_low_confidence_retry_candidate(right)


def _normalize_low_confidence_retry_candidate(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _LOW_CONFIDENCE_RETRY_EQUIVALENT_PATTERN.sub("", normalized)


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


_PendingLowConfidence = tuple[_SegmentPayload, str, tuple[str, ...]]


class TranslationQualityFailureError(RuntimeError):
    pass


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


def _low_confidence_retry_budget_for_attempt(
    low_confidence_max_retries: int,
    subtask_attempt: int,
) -> int:
    total_budget = (
        max(0, low_confidence_max_retries)
        * _LOW_CONFIDENCE_RETRY_CALLS_PER_CONFIGURED_RETRY
    )
    if subtask_attempt <= 1:
        return total_budget
    return min(total_budget, _LOW_CONFIDENCE_RETRY_ATTEMPT_BUDGET)


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
    transport_retry_attempts: int = 3

    async def run(self, subtask: Subtask) -> SubtaskResult:
        chunk, metadata = _decode_subtask_payload(subtask.request_payload)
        return await self._attempt(
            chunk,
            metadata,
            subtask.id,
            subtask_attempt=subtask.attempt_count,
        )

    async def _attempt(
        self,
        chunk: TranslationChunk,
        metadata: tuple[_SegmentPayload, ...],
        subtask_id: str = "",
        *,
        subtask_attempt: int = 1,
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
        retries_remaining = _PARTIAL_ACCEPT_MAX_RETRIES
        total_input = 0
        total_output = 0
        total_cached_input = 0
        last_raw = ""
        first_user_prompt = ""
        finalized: dict[str, str] = {}
        finalized_reasons: dict[str, tuple[str, ...]] = {}
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
                            transport_retry_attempts=self.transport_retry_attempts,
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
                total_cached_input += response.usage.cached_input_tokens
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

            mass_source_residue_rescued = False
            while True:
                pending: list[
                    tuple[_SegmentPayload, str, tuple[str, ...]]
                ] = []
                finalized = {}
                finalized_reasons = {}
                low_confidence = []
                mass_source_residue_count = 0
                mass_source_residue_meta: list[_SegmentPayload] = []
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
                    verdict = self._evaluate_confidence(
                        meta.original_text, final_text
                    )
                    extra_reasons: list[str] = []
                    if fallback_reason:
                        extra_reasons.append(fallback_reason)
                    all_reasons = tuple(extra_reasons) + verdict.reasons
                    if _is_mass_source_residue_candidate(
                        meta, final_text, all_reasons, self.source_language
                    ):
                        mass_source_residue_count += 1
                        mass_source_residue_meta.append(meta)
                    if (
                        (verdict.is_low_confidence or fallback_reason)
                        and self.low_confidence_max_retries > 0
                    ):
                        pending.append((meta, final_text, all_reasons))
                        continue
                    finalized[meta.segment_id] = final_text
                    finalized_reasons[meta.segment_id] = all_reasons
                    if verdict.is_low_confidence or extra_reasons:
                        entry: dict[str, object] = {
                            "segment_id": meta.segment_id,
                            "reasons": extra_reasons + list(verdict.reasons),
                        }
                        tags = list(verdict.tags)
                        if fallback_reason and "source_residue" not in tags:
                            tags.append("source_residue")
                        if tags:
                            entry["tags"] = tags
                        low_confidence.append(entry)

                if not _should_fail_for_mass_source_residue(
                    mass_source_residue_count,
                    len(metadata),
                    include_small_all=False,
                ):
                    break

                if mass_source_residue_rescued:
                    for meta in mass_source_residue_meta:
                        fallback_reasons_by_index.setdefault(
                            meta.chunk_index,
                            "mass_source_residue_after_batch_retry_exhausted",
                        )
                    break
                mass_source_residue_rescued = True
                rescue_chunk = _build_subchunk_from_pending(
                    chunk,
                    tuple(mass_source_residue_meta),
                    include_context=False,
                )
                rescue_user_prompt = self._compose_user_prompt(
                    self._apply_roster(assemble_user_prompt(rescue_chunk)),
                    format_retry=False,
                    source_residue_retry=True,
                )
                rescue_request_model = _with_translation_request_timeout(
                    self.model, "partial_retry"
                )

                async def _rescue_llm_call() -> object:
                    return await self._one_llm_call(
                        system_prompt,
                        rescue_user_prompt,
                        f"{log_label} source-residue-retry",
                        model=rescue_request_model,
                    )

                try:
                    rescue_response = await asyncio.wait_for(
                        retry_async(
                            _rescue_llm_call,
                            transport_retry_attempts=self.transport_retry_attempts,
                            max_retry_attempts=_RESCUE_TRANSPORT_RETRY_BUDGET,
                            max_transport_retry_attempts=_transport_retry_budget(
                                self.model
                            ),
                            should_retry=lambda exc: _should_retry_translation_error(
                                self.model, exc
                            ),
                        ),
                        timeout=rescue_request_model.timeout_seconds,
                    )
                except BaseException as exc:
                    debug_attempts.append(
                        {
                            "phase": "mass_source_residue_retry",
                            "user_prompt": rescue_user_prompt,
                            "timeout_seconds": rescue_request_model.timeout_seconds,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    if not is_transient_llm_error(exc):
                        raise
                    for meta in mass_source_residue_meta:
                        fallback_reasons_by_index.setdefault(
                            meta.chunk_index,
                            "mass_source_residue_retry_transient_failed",
                        )
                    break

                total_input += rescue_response.usage.input_tokens
                total_output += rescue_response.usage.output_tokens
                total_cached_input += rescue_response.usage.cached_input_tokens
                rescue_raw = self._restore_roster(rescue_response.content)
                last_raw = rescue_raw
                debug_attempts.append(
                    {
                        "phase": "mass_source_residue_retry",
                        "user_prompt": rescue_user_prompt,
                        "timeout_seconds": rescue_request_model.timeout_seconds,
                        "raw_response": rescue_raw,
                    }
                )
                rescued_translations, _missing = self._decode_partial(
                    rescue_raw,
                    mass_source_residue_meta,
                    rescue_chunk.context_lines,
                )
                for idx, text in rescued_translations.items():
                    accumulated[idx] = text
                    fallback_reasons_by_index.pop(idx, None)

            # Low-confidence rows share one paid rescue budget for the chunk
            # lifecycle. Runtime subtask retries get only one rescue call,
            # keeping pathological chunks bounded while preserving a small
            # quality repair path after a failed attempt. Larger pending sets
            # get one compact micro-batch pass first; any leftovers consume the
            # same budget in the existing focused solo path.
            retry_call_budget = _low_confidence_retry_budget_for_attempt(
                self.low_confidence_max_retries,
                subtask_attempt,
            )
            if pending and retry_call_budget > 0:
                micro_batches, micro_leftovers = (
                    _split_low_confidence_micro_batches(pending)
                )
                batched_pending: list[_PendingLowConfidence] = list(
                    micro_leftovers
                )
                for micro_batch in micro_batches:
                    if retry_call_budget <= 0:
                        batched_pending.extend(micro_batch)
                        continue
                    retry_call_budget -= 1
                    retry_round = max(retry_round, 1)
                    for meta, _last_text, _last_reasons in micro_batch:
                        solo_retried_indices.add(meta.chunk_index)
                    micro_meta = tuple(
                        meta for meta, _last_text, _last_reasons in micro_batch
                    )
                    micro_chunk = _build_subchunk_from_pending(
                        chunk,
                        micro_meta,
                        include_context=False,
                    )
                    micro_user_prompt = self._compose_user_prompt(
                        self._apply_roster(assemble_user_prompt(micro_chunk)),
                        format_retry=False,
                        low_confidence_retry=True,
                    )
                    micro_request_model = _with_translation_request_timeout(
                        self.model, "partial_retry"
                    )

                    async def _micro_llm_call() -> object:
                        return await self._one_llm_call(
                            system_prompt,
                            micro_user_prompt,
                            f"{log_label} low-confidence-batch-retry",
                            model=micro_request_model,
                        )

                    try:
                        micro_response = await asyncio.wait_for(
                            retry_async(
                                _micro_llm_call,
                                transport_retry_attempts=self.transport_retry_attempts,
                                max_retry_attempts=_RESCUE_TRANSPORT_RETRY_BUDGET,
                                max_transport_retry_attempts=_transport_retry_budget(
                                    self.model
                                ),
                                should_retry=lambda exc: _should_retry_translation_error(
                                    self.model, exc
                                ),
                            ),
                            timeout=micro_request_model.timeout_seconds,
                        )
                    except (LlmRequestError, TimeoutError) as exc:
                        if not is_transient_llm_error(exc):
                            raise
                        debug_attempts.append(
                            {
                                "phase": "low_confidence_batch_retry",
                                "user_prompt": micro_user_prompt,
                                "segment_ids": [
                                    meta.segment_id for meta in micro_meta
                                ],
                                "timeout_seconds": micro_request_model.timeout_seconds,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        for meta, last_text, last_reasons in micro_batch:
                            batched_pending.append(
                                (
                                    meta,
                                    last_text,
                                    tuple(last_reasons)
                                    + (
                                        "low_confidence_batch_retry_transient_failed",
                                    ),
                                )
                            )
                        continue

                    total_input += micro_response.usage.input_tokens
                    total_output += micro_response.usage.output_tokens
                    total_cached_input += micro_response.usage.cached_input_tokens
                    micro_raw = self._restore_roster(micro_response.content)
                    debug_attempts.append(
                        {
                            "phase": "low_confidence_batch_retry",
                            "user_prompt": micro_user_prompt,
                            "raw_response": micro_raw,
                            "segment_ids": [
                                meta.segment_id for meta in micro_meta
                            ],
                            "timeout_seconds": micro_request_model.timeout_seconds,
                        }
                    )
                    micro_translations, _missing = self._decode_partial(
                        micro_raw,
                        micro_meta,
                        micro_chunk.context_lines,
                    )
                    for meta, last_text, last_reasons in micro_batch:
                        current_text = last_text
                        current_reasons = last_reasons
                        retry_text = micro_translations.get(meta.chunk_index)
                        if retry_text is not None:
                            retry_final = self._postprocess(meta, retry_text)
                            verdict = self._evaluate_confidence(
                                meta.original_text, retry_final
                            )
                            if not verdict.is_low_confidence:
                                finalized[meta.segment_id] = retry_final
                                finalized_reasons[meta.segment_id] = verdict.reasons
                                continue
                            if _should_replace_low_confidence_candidate(
                                meta,
                                current_text,
                                current_reasons,
                                retry_final,
                                verdict.reasons,
                                self.source_language,
                                self.target_language,
                            ):
                                current_text = retry_final
                                current_reasons = verdict.reasons
                        batched_pending.append(
                            (meta, current_text, current_reasons)
                        )
                if micro_batches:
                    pending = batched_pending

            still_pending: list[_PendingLowConfidence] = []
            for meta, last_text, last_reasons in pending:
                solo_retried_indices.add(meta.chunk_index)
                current_text = last_text
                current_reasons = last_reasons
                for solo_round in range(self.low_confidence_max_retries):
                    if retry_call_budget <= 0:
                        break
                    retry_call_budget -= 1
                    retry_round = max(retry_round, solo_round + 1)
                    # Mirror the proofreading-page "retranslate" path
                    # exactly: chunk_index=0 (model sees an isolated
                    # single-line task, not "line N of some chunk"),
                    # no context_lines (no neighboring segments to
                    # compete for attention), glossary entries matched
                    # against just this one source. This eliminates the
                    # batch-context drift where the model keyed a
                    # neighbor's translation under the wrong index.
                    solo_glossary_entries = _match_retry_glossary(
                        chunk,
                        (meta.prompt_text,),
                    )
                    solo_chunk = TranslationChunk(
                        segments=(
                            ChunkSegment(
                                segment_id=meta.segment_id,
                                chunk_index=0,
                                prompt_text=meta.prompt_text,
                            ),
                        ),
                        context_lines=(),
                        glossary_entries=solo_glossary_entries,
                    )
                    solo_user_prompt = self._compose_user_prompt(
                        self._apply_roster(assemble_user_prompt(solo_chunk)),
                        format_retry=False,
                        low_confidence_retry=True,
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
                                    transport_retry_attempts=self.transport_retry_attempts,
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
                                        transport_retry_attempts=self.transport_retry_attempts,
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
                    total_cached_input += solo_response.usage.cached_input_tokens
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
                        finalized_reasons[meta.segment_id] = verdict.reasons
                        current_text = None  # signal: passed
                        break
                    if (
                        retry_final.strip() == current_text.strip()
                        or _same_low_confidence_retry_candidate(
                            retry_final, current_text
                        )
                    ):
                        current_text = retry_final
                        current_reasons = verdict.reasons
                        break
                    # Still low-conf: keep the best candidate seen so final
                    # quality exhaustion preserves useful target-language text.
                    if _should_replace_low_confidence_candidate(
                        meta,
                        current_text,
                        current_reasons,
                        retry_final,
                        verdict.reasons,
                        self.source_language,
                        self.target_language,
                    ):
                        current_text = retry_final
                        current_reasons = verdict.reasons
                if current_text is not None:
                    still_pending.append((meta, current_text, current_reasons))
            pending = still_pending

            for meta, last_text, last_reasons in pending:
                final_verdict = self._evaluate_confidence(
                    meta.original_text, last_text
                )
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
                # - Model produced a target-language guess that's flawed but not
                #   residue: keep it. A questionable translated line is
                #   easier to fix than re-translating from scratch.
                tags = list(final_verdict.tags)
                if has_residue or echoes_source:
                    if "source_residue" not in tags:
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
                finalized_reasons[meta.segment_id] = (
                    tuple(last_reasons) + (extra_reason,)
                )
                entry: dict[str, object] = {
                    "segment_id": meta.segment_id,
                    "reasons": list(last_reasons) + [extra_reason],
                }
                if tags:
                    entry["tags"] = tags
                low_confidence.append(entry)

            terminal_source_residue_count = 0
            for meta in metadata:
                final_text = finalized.get(meta.segment_id)
                if final_text is None:
                    continue
                if _is_mass_source_residue_candidate(
                    meta,
                    final_text,
                    finalized_reasons.get(meta.segment_id, ()),
                    self.source_language,
                ):
                    terminal_source_residue_count += 1
            if _should_fail_for_mass_source_residue(
                terminal_source_residue_count,
                len(metadata),
                include_small_all=True,
            ):
                for meta in metadata:
                    final_text = finalized.get(meta.segment_id)
                    if final_text is None:
                        continue
                    if not _is_mass_source_residue_candidate(
                        meta,
                        final_text,
                        finalized_reasons.get(meta.segment_id, ()),
                        self.source_language,
                    ):
                        continue
                    finalized_reasons[meta.segment_id] = (
                        finalized_reasons.get(meta.segment_id, ())
                        + ("mass_source_residue_after_retry",)
                    )
                    _upsert_low_confidence_entry(
                        low_confidence,
                        segment_id=meta.segment_id,
                        reasons=("mass_source_residue_after_retry",),
                        tags=("source_residue",),
                    )

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
                        for tag in verdict.tags:
                            if tag not in tags:
                                tags.append(tag)
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
                cached_input_tokens=total_cached_input,
            )
        except BaseException as exc:
            terminal_error = exc
            if isinstance(exc, TranslationQualityFailureError):
                error_text = f"{type(exc).__name__}: {exc}"
                response_text = error_text
                if last_raw:
                    response_text = (
                        f"{error_text}\n\n--- Last model response ---\n{last_raw}"
                    )
                append_local_failure(
                    label=f"{log_label} local validation",
                    error=error_text,
                    response_text=response_text,
                )
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
                        "cached_input_tokens": total_cached_input,
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
        source_residue_retry: bool = False,
        low_confidence_retry: bool = False,
    ) -> str:
        # Format contract lives in the system prompt (built-in suffix or
        # ``_augment_system_prompt``'s injected hint) so we don't repeat
        # it in every user message — that overhead was 3-5x amplified
        # by small chunk sizes. The retry banner still prepends here on
        # the second-and-later attempts.
        reminders: list[str] = []
        if format_retry:
            reminders.append(_FORMAT_RETRY_REMINDER)
        if source_residue_retry:
            reminders.append(
                _SOURCE_RESIDUE_RETRY_REMINDER.format(
                    target_language=language_prompt_label(self.target_language)
                )
            )
        if low_confidence_retry:
            reminders.append(
                _LOW_CONFIDENCE_RETRY_REMINDER.format(
                    target_language=language_prompt_label(self.target_language)
                )
            )
        if reminders:
            return "\n\n".join((*reminders, body))
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
            if (
                _looks_truncated_jsonl_response(raw_content)
                and len(decoded.lines) > 1
            ):
                complete_prefix = decoded.lines[:-1]
                translations_by_index = {
                    line.index: line.text
                    for line in complete_prefix
                    if line.index in expected
                }
                return (
                    translations_by_index,
                    frozenset(expected - translations_by_index.keys()),
                )
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


def _upsert_low_confidence_entry(
    entries: list[dict[str, object]],
    *,
    segment_id: str,
    reasons: Sequence[str] = (),
    tags: Sequence[str] = (),
) -> None:
    for entry in entries:
        if entry.get("segment_id") != segment_id:
            continue
        existing_reasons = entry.get("reasons")
        if not isinstance(existing_reasons, list):
            existing_reasons = []
            entry["reasons"] = existing_reasons
        for reason in reasons:
            if reason not in existing_reasons:
                existing_reasons.append(reason)
        existing_tags = entry.get("tags")
        if tags and not isinstance(existing_tags, list):
            existing_tags = []
            entry["tags"] = existing_tags
        if isinstance(existing_tags, list):
            for tag in tags:
                if tag not in existing_tags:
                    existing_tags.append(tag)
        return

    entry: dict[str, object] = {
        "segment_id": segment_id,
        "reasons": list(reasons),
    }
    if tags:
        entry["tags"] = list(tags)
    entries.append(entry)


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
_CJK_TARGET_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_JAPANESE_TARGET_RE = re.compile(
    f"{_JAPANESE_RESIDUE_RE.pattern}|{_CJK_TARGET_RE.pattern}"
)
_LATIN_TARGET_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_ARABIC_TARGET_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
_CYRILLIC_TARGET_RE = re.compile(r"[\u0400-\u04ff]")
_THAI_TARGET_RE = re.compile(r"[\u0e00-\u0e7f]")


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


def _script_ratio(text: str, pattern: re.Pattern[str]) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return len(pattern.findall("".join(letters))) / max(1, len(letters))


def _target_language_score(text: str, target_language: Language) -> float:
    if target_language in (
        Language.CHINESE_SIMPLIFIED,
        Language.CHINESE_TRADITIONAL,
    ):
        return _script_ratio(text, _CJK_TARGET_RE)
    if target_language is Language.KOREAN:
        return _script_ratio(text, _KOREAN_RESIDUE_RE)
    if target_language is Language.JAPANESE:
        return _script_ratio(text, _JAPANESE_TARGET_RE)
    if target_language is Language.RUSSIAN:
        return _script_ratio(text, _CYRILLIC_TARGET_RE)
    if target_language is Language.ARABIC:
        return _script_ratio(text, _ARABIC_TARGET_RE)
    if target_language is Language.THAI:
        return _script_ratio(text, _THAI_TARGET_RE)
    return _script_ratio(text, _LATIN_TARGET_RE)


def _low_confidence_candidate_rank(
    meta: _SegmentPayload,
    text: str,
    reasons: Sequence[str],
    source_language: Language,
    target_language: Language,
) -> tuple[float, ...]:
    stripped = text.strip()
    reason_text = " ".join(reasons).lower()
    source_similarity = _text_similarity(stripped, meta.original_text)
    return (
        1.0 if stripped else 0.0,
        0.0 if "model_chatter" in reason_text else 1.0,
        0.0 if stripped == meta.original_text.strip() else 1.0,
        0.0
        if _is_mass_source_residue_candidate(
            meta, stripped, reasons, source_language
        )
        else 1.0,
        _target_language_score(stripped, target_language),
        -_residue_score(stripped, source_language),
        -source_similarity,
        -float(len(reasons)),
    )


def _should_replace_low_confidence_candidate(
    meta: _SegmentPayload,
    current_text: str,
    current_reasons: Sequence[str],
    retry_text: str,
    retry_reasons: Sequence[str],
    source_language: Language,
    target_language: Language,
) -> bool:
    return _low_confidence_candidate_rank(
        meta,
        retry_text,
        retry_reasons,
        source_language,
        target_language,
    ) > _low_confidence_candidate_rank(
        meta,
        current_text,
        current_reasons,
        source_language,
        target_language,
    )


def _is_mass_source_residue_candidate(
    meta: _SegmentPayload,
    text: str,
    reasons: Sequence[str],
    source_language: Language,
) -> bool:
    reason_text = " ".join(reasons).lower()
    if text.strip() == meta.original_text.strip():
        return "residue" in reason_text or "too similar" in reason_text
    if "residue" not in reason_text and "too similar" not in reason_text:
        return False
    if _text_similarity(text, meta.original_text) >= 0.92:
        return True
    return _residue_score(text, source_language) >= 0.5


def _should_fail_for_mass_source_residue(
    count: int,
    total: int,
    *,
    include_small_all: bool,
) -> bool:
    if total <= 0:
        return False
    if (
        include_small_all
        and 1 < total < _MASS_SOURCE_RESIDUE_MIN_SEGMENTS
        and count == total
    ):
        return True
    return (
        count >= _MASS_SOURCE_RESIDUE_MIN_SEGMENTS
        and count / total >= _MASS_SOURCE_RESIDUE_RATIO
    )


def _split_low_confidence_micro_batches(
    pending: Sequence[_PendingLowConfidence],
) -> tuple[
    list[tuple[_PendingLowConfidence, ...]],
    tuple[_PendingLowConfidence, ...],
]:
    if len(pending) < _LOW_CONFIDENCE_MICRO_BATCH_MIN_SEGMENTS:
        return [], tuple(pending)

    batches: list[tuple[_PendingLowConfidence, ...]] = []
    leftovers: list[_PendingLowConfidence] = []
    current: list[_PendingLowConfidence] = []
    current_tokens = 0
    for item in pending:
        meta = item[0]
        item_tokens = max(1, estimate_tokens_from_text(meta.prompt_text))
        if current and (
            len(current) >= _LOW_CONFIDENCE_MICRO_BATCH_MAX_SEGMENTS
            or current_tokens + item_tokens > _LOW_CONFIDENCE_MICRO_BATCH_TOKEN_CAP
        ):
            if len(current) >= _LOW_CONFIDENCE_MICRO_BATCH_MIN_SEGMENTS:
                batches.append(tuple(current))
            else:
                leftovers.extend(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += item_tokens

    if current:
        if len(current) >= _LOW_CONFIDENCE_MICRO_BATCH_MIN_SEGMENTS:
            batches.append(tuple(current))
        else:
            leftovers.extend(current)

    return batches, tuple(leftovers)


def _build_subchunk_from_pending(
    original: TranslationChunk,
    pending: Sequence[_SegmentPayload],
    *,
    include_context: bool = True,
) -> TranslationChunk:
    pending_indices = {m.chunk_index for m in pending}
    pending_segments = tuple(
        seg for seg in original.segments if seg.chunk_index in pending_indices
    )
    return TranslationChunk(
        segments=pending_segments,
        context_lines=original.context_lines if include_context else (),
        glossary_entries=_match_retry_glossary(
            original,
            (seg.prompt_text for seg in pending_segments),
        ),
    )


def _match_retry_glossary(
    original: TranslationChunk,
    source_texts: Iterable[str],
) -> tuple[GlossaryEntry, ...]:
    if not original.glossary_entries:
        return ()
    return Glossary(entries=original.glossary_entries).match_many(source_texts)


__all__ = [
    "SUBTASK_PAYLOAD_VERSION",
    "TranslationSubtaskRunner",
    "encode_subtask_payload",
]
