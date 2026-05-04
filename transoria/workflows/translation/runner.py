"""Translation subtask runner: prompt → LLM → decode → postprocess.

The orchestrator builds chunks, serializes each into a subtask payload, and
hands the executor a runner instance. The executor calls
:meth:`TranslationSubtaskRunner.run` for every subtask. This file is the
narrowest place that talks to the LLM; everything above it deals in
preprocessed strings and translation results.
"""

from __future__ import annotations

import json
import re
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


_JSONL_KEYWORD_PATTERN = re.compile(
    r"jsonl(?:ine)?|\{\s*\"\s*<\s*INDEX", re.IGNORECASE
)


def _augment_system_prompt(
    system_prompt: str, preset: PromptPreset
) -> str:
    """Inject the runtime format-transport contract into a custom
    preset's system message when the preset itself does not mention it.

    System (built-in) presets already carry the JSONLINE suffix, so
    they're left alone. User-authored presets focus on style/voice and
    rarely include the wire format — without this nudge the model can
    treat the long literary system prompt as the strongest signal and
    return prose, even though the user message still asks for JSONLINE.

    The hint is strictly about transport, not extraction policy.
    """

    if getattr(preset, "is_system", False):
        return system_prompt
    if _JSONL_KEYWORD_PATTERN.search(system_prompt):
        return system_prompt
    return system_prompt + _SYSTEM_FORMAT_CONTRACT_HINT


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
    min_length_ratio: float = 0.25
    max_length_ratio: float = 4.0
    max_punctuation_delta: int = 12
    low_confidence_max_retries: int = 0
    tpm_limiter: TpmLimiter | None = None
    key_pool: KeyPool | None = None
    stream: bool = False
    debug_log_dir: Path | None = None
    fake_name_roster: FakeNameRoster | FakeNameSession | None = None

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
                    source_language=self.source_language.value,
                    target_language=self.target_language.value,
                ),
                thinking=self.model.thinking_prompt_enabled,
            ),
            self.prompt_preset,
        )
        log_label = f"translation {subtask_id}" if subtask_id else "translation"

        accumulated: dict[int, str] = {}
        rescued_indices: set[int] = set()
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
                sub_chunk = _build_subchunk_from_pending(chunk, pending_meta)
                user_prompt = self._compose_user_prompt(
                    self._apply_roster(assemble_user_prompt(sub_chunk)),
                    format_retry=len(debug_attempts) > 0,
                )
                if not first_user_prompt:
                    first_user_prompt = user_prompt

                async def _llm_call() -> object:
                    return await self._one_llm_call(
                        system_prompt, user_prompt, log_label
                    )

                response = await retry_async(_llm_call, model=self.model)
                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens
                raw_content = self._restore_roster(response.content)
                last_raw = raw_content
                debug_attempts.append(
                    {"user_prompt": user_prompt, "raw_response": raw_content}
                )

                translations, missing = self._decode_partial(
                    raw_content, pending_meta
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
                    pending_meta = tuple(
                        metadata_by_index[i] for i in sorted(pending_indices)
                    )
                    rescued = _positional_rescue(last_raw, pending_meta)
                    if rescued is not None:
                        for idx, text in rescued.items():
                            accumulated[idx] = text
                            rescued_indices.add(idx)
                        pending_indices.clear()
                        break
                    # If everything that's pending is the duplicate-drift
                    # set (no missing indices left), surface that as the
                    # cause; otherwise the line-count-mismatch wins.
                    if all(idx in metadata_by_index for idx in pending_indices) \
                            and not missing:
                        raise LlmRequestError(
                            "Translation duplicate drift — indices "
                            f"{sorted(pending_indices)} share identical "
                            "translation across distinct sources",
                            code="llm.duplicate_translations",
                        )
                    raise LlmRequestError(
                        "Translation line count mismatch — could not recover "
                        f"{sorted(pending_indices)} after "
                        f"{len(debug_attempts)} attempt(s)",
                        code="llm.line_count_mismatch",
                    )
                retries_remaining -= 1

            pending: list[tuple[_SegmentPayload, str, tuple[str, ...]]] = []

            for meta in metadata:
                final_text = self._postprocess(
                    meta, accumulated[meta.chunk_index]
                )
                verdict = self._evaluate_confidence(meta.original_text, final_text)
                extra_reasons: list[str] = []
                if meta.chunk_index in rescued_indices:
                    extra_reasons.append("positional_rescue_after_format_failure")
                if verdict.is_low_confidence and self.low_confidence_max_retries > 0:
                    pending.append(
                        (meta, final_text, tuple(extra_reasons) + verdict.reasons)
                    )
                    continue
                finalized[meta.segment_id] = final_text
                if verdict.is_low_confidence or extra_reasons:
                    low_confidence.append(
                        {
                            "segment_id": meta.segment_id,
                            "reasons": extra_reasons + list(verdict.reasons),
                        }
                    )

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
                debug_attempts.append(
                    {"user_prompt": retry_user_prompt, "raw_response": retry_raw}
                )
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
                        # Don't overwrite the initial candidate with a still-failing
                        # retry — the model often hallucinates worse content the
                        # second time around.
                        next_pending.append((meta, last_text, last_reasons))
                        continue
                    finalized[meta.segment_id] = retry_final
                pending = next_pending

            for meta, _last_text, last_reasons in pending:
                # No retry passed confidence: fall back to the source text so
                # the user sees the original line in the output and can fix
                # it in the proofreading page. Never silently store the
                # low-confidence candidate as if it were a clean translation.
                finalized[meta.segment_id] = meta.original_text
                low_confidence.append(
                    {
                        "segment_id": meta.segment_id,
                        "reasons": list(last_reasons) + ["fell_back_to_source_after_max_retries"],
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

    def _decode_partial(
        self,
        raw_content: str,
        metadata: Sequence[_SegmentPayload],
    ) -> tuple[dict[int, str], frozenset[int]]:
        """Decode the LLM response into ``{chunk_index: text}`` and the
        set of expected indices that did not appear.

        Indices outside the expected set are silently dropped — happens
        when a partial-retry call asks for a small subset and the model
        echoes the full prior chunk; we accept the requested indices
        and ignore the stale extras. Duplicate-drift detection runs on
        the accumulated dict in the caller, not here.
        """

        decoded = decode_translation_jsonl(raw_content)
        expected = {meta.chunk_index for meta in metadata}
        translations_by_index = {
            line.index: line.text
            for line in decoded.lines
            if line.index in expected
        }
        missing = frozenset(expected - translations_by_index.keys())
        return translations_by_index, missing

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


_THINKING_RESCUE_PATTERN = re.compile(
    r"<(?:why|think)>.*?</(?:why|think)>", re.DOTALL | re.IGNORECASE
)
_FENCE_RESCUE_PATTERN = re.compile(
    r"```(?:jsonline|jsonl|json|markdown|md)?\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
_RESCUE_PROSE_REJECT_PATTERN = re.compile(r"^\s*[\{\[]")


_DUPLICATE_DRIFT_MIN_TEXT_LENGTH = 5


def _detect_duplicate_drift(
    translations_by_index: dict[int, str],
    metadata: tuple["_SegmentPayload", ...],
) -> list[int]:
    """Flag indices that look like model-laziness duplicate output.

    A short translation can legitimately collide across distinct sources
    (Korean 응/어/네 → 嗯。/好。, English yeah/yep → 是的, etc.) so we
    require either translation length ≥ 5 chars OR three+ distinct
    sources sharing one output before treating it as drift. The 5-char
    floor and 3-source threshold are empirical: real "model copy-pastes
    one sentence across the chunk" failures produce many cells with a
    long shared translation; natural-language coincidences cap at 2-3
    affirmatives.
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
        if len(text) < _DUPLICATE_DRIFT_MIN_TEXT_LENGTH and len(sources) < 3:
            continue
        suspicious.update(indices)
    return sorted(suspicious)


def _build_subchunk_from_pending(
    original: TranslationChunk,
    pending: Sequence[_SegmentPayload],
) -> TranslationChunk:
    pending_indices = {m.chunk_index for m in pending}
    return TranslationChunk(
        segments=tuple(
            seg for seg in original.segments if seg.chunk_index in pending_indices
        ),
        context_lines=original.context_lines,
        glossary_entries=original.glossary_entries,
    )


def _positional_rescue(
    raw: str, metadata: tuple["_SegmentPayload", ...]
) -> dict[int, str] | None:
    """Last-resort accept of plain-text translations by source position.

    Only triggers when the cleaned response has *exactly* one non-empty
    line per expected segment AND none of those lines look like JSON
    fragments. The strict shape predicate prevents silently aligning
    against a half-broken JSON response (which would corrupt the result
    instead of failing loudly).
    """

    if not metadata:
        return None
    cleaned = _THINKING_RESCUE_PATTERN.sub("", raw)
    fence = _FENCE_RESCUE_PATTERN.search(cleaned)
    if fence is not None:
        cleaned = fence.group(1)
    candidate_lines = [
        line.strip() for line in cleaned.splitlines() if line.strip()
    ]
    if len(candidate_lines) != len(metadata):
        return None
    for line in candidate_lines:
        if _RESCUE_PROSE_REJECT_PATTERN.match(line):
            # Looks like a half-parsed JSON fragment — refuse to guess.
            return None
    ordered_meta = sorted(metadata, key=lambda m: m.chunk_index)
    return {
        meta.chunk_index: text
        for meta, text in zip(ordered_meta, candidate_lines)
    }


__all__ = [
    "SUBTASK_PAYLOAD_VERSION",
    "TranslationSubtaskRunner",
    "encode_subtask_payload",
]
