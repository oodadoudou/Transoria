"""Glossary-extraction subtask runner: prompt → LLM → decode → persist.

Unlike translation, the runner does not enforce any output-line-count
constraint: each chunk produces zero or more candidate entries. Decode
issues are recorded in the subtask payload so the orchestrator can surface
them in the run statistics file without losing the rest of the chunk's
output.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from transoria.domain import Language, language_prompt_label
from transoria.llm.client import ChatRequest, LlmClient, LlmRequestError
from transoria.llm.config import ModelConfig
from transoria.llm.decoders import DecodeIssue, GlossaryEntry, decode_glossary_jsonl
from transoria.llm.retry import is_transient_llm_error, retry_async
from transoria.llm.usage import estimate_tokens_from_text
from transoria.prompts import PromptContext, PromptPreset, build_prompt
from transoria.runtime.executor import SubtaskResult
from transoria.runtime.key_pool import KeyPool
from transoria.runtime.rate_limit import TpmLimiter
from transoria.runtime.subtask import Subtask
from transoria.workflows.debug_log import write_subtask_debug_log
from transoria.workflows.fake_name import FakeNameSession
from transoria.workflows.glossary.chunker import GlossaryChunk


class _GlossaryFormatRetry(Exception):
    """Raised when the LLM produced a substantial response but the
    decoder (JSONL + header-aware salvage) extracted zero entries.

    Triggers retry: a fresh sampling pass often complies with the
    JSONL contract because temperature > 0 makes each call vary.
    """


# Threshold for "the LLM said real things but in the wrong format".
# Below this, an empty response is more plausibly a chunk that legitimately
# has no extractable terms — retrying would just waste tokens.
_FORMAT_RETRY_MIN_ISSUES = 3
_FORMAT_RETRY_REMINDER = (
    "FORMAT RETRY: the previous answer was rejected because it contained "
    "prose, headings, Markdown, non-JSON text, or empty type values. "
    "Output JSONLINE only. The first non-whitespace character must be \"{\". "
    'Each object must include non-empty "src", "dst", and "type" values.'
)
_TRANSPORT_RETRY_BUDGET = 4
_SOFT_TIMEOUT_SECONDS = 90.0
_HIGH_CONCURRENCY_MAX_SPLIT_DEPTH = 1


def _output_contract_reminder(target_language: str) -> str:
    """Runtime-level output contract appended to every glossary call.

    Lives here (not in the preset) so the same hard rules apply
    regardless of which preset (system or user-custom) is active —
    custom prompts shouldn't be able to drift the schema or let the
    LLM emit mixed-language ``type`` values. The language clause is
    parameterized so it tracks the user's currently selected target
    language without the prompt author having to remember to set it.
    """

    return (
        "FINAL OUTPUT CONTRACT: output JSONLINE only. Each line must be one JSON "
        'object with exactly these keys: "src", "dst", "type". The "dst" and "type" values '
        "must be non-empty and follow the active prompt's taxonomy. "
        "If the active prompt decides a candidate should be filtered, excluded, "
        "deleted, skipped, ignored, or not extracted, omit that candidate entirely; "
        "never output it as a glossary row with a filtered/excluded, generic, common, "
        "ordinary, optional, or low-value category. "
        f'The "dst" and "type" values must always be written in {target_language} — '
        "never mix languages, never fall back to English category names. "
        "No prose, no Markdown, no code fence."
    )


def _should_retry_glossary_request(model: ModelConfig, exc: BaseException) -> bool:
    if isinstance(exc, _GlossaryFormatRetry):
        return True
    if (
        isinstance(exc, LlmRequestError)
        and getattr(exc, "code", "") == "llm.transport_error"
        and "timeout" in str(exc).lower()
    ):
        return False
    return is_transient_llm_error(exc)


SUBTASK_PAYLOAD_VERSION = 1


def encode_glossary_payload(chunk: GlossaryChunk) -> dict[str, object]:
    return {
        "version": SUBTASK_PAYLOAD_VERSION,
        **chunk.to_payload(),
    }


def _decode_chunk(payload: Mapping[str, object]) -> tuple[str, str, str]:
    chunk_id = str(payload.get("chunk_id", ""))
    source_file = str(payload.get("source_file", ""))
    text = str(payload.get("text", ""))
    if not chunk_id or not text:
        raise ValueError(f"Invalid glossary subtask payload: {payload!r}")
    return chunk_id, source_file, text


@dataclass(frozen=True)
class GlossarySubtaskRunner:
    client: LlmClient
    model: ModelConfig
    prompt_preset: PromptPreset
    source_language: Language
    target_language: Language
    tpm_limiter: TpmLimiter | None = None
    key_pool: KeyPool | None = None
    stream: bool = False
    debug_log_dir: Path | None = None
    fake_name_session: FakeNameSession | None = None
    name_injections: Mapping[str, str] | None = None
    novel_background: str = ""

    async def run(self, subtask: Subtask) -> SubtaskResult:
        chunk_id, source_file, text = _decode_chunk(subtask.request_payload)
        return await self._run_resilient(chunk_id, source_file, text, depth=0)

    async def _run_resilient(
        self, chunk_id: str, source_file: str, text: str, *, depth: int
    ) -> SubtaskResult:
        try:
            return await self._run_single(chunk_id, source_file, text)
        except LlmRequestError as exc:
            if not _should_split_after_transport_timeout(self.model, exc, depth):
                raise
            rescue_parts = _split_text_in_half(text)
            if len(rescue_parts) <= 1:
                raise
            return await self._run_split_parts(
                chunk_id,
                source_file,
                rescue_parts,
                suffix="r",
                depth=depth,
            )

    async def _run_split_parts(
        self,
        chunk_id: str,
        source_file: str,
        parts: tuple[str, ...],
        *,
        suffix: str,
        depth: int,
    ) -> SubtaskResult:
        completed_results: list[SubtaskResult] = []
        for index, part in enumerate(parts, start=1):
            completed_results.append(
                await self._run_resilient(
                    f"{chunk_id}.{suffix}{index}",
                    source_file,
                    part,
                    depth=depth + 1,
                )
            )
        return _merge_glossary_results(completed_results)

    async def _run_single(
        self, chunk_id: str, source_file: str, text: str
    ) -> SubtaskResult:
        attempt_index = -1
        best_result: SubtaskResult | None = None
        best_score: tuple[int, int, int] | None = None

        async def operation() -> SubtaskResult:
            nonlocal attempt_index, best_result, best_score
            attempt_index += 1
            try:
                result = await self._attempt(
                    chunk_id, source_file, text, attempt_index=attempt_index
                )
            except _GlossaryFormatRetry as exc:
                if exc.args and isinstance(exc.args[0], SubtaskResult):
                    result = exc.args[0]
                    score = _score_glossary_result(result)
                    if best_score is None or score > best_score:
                        best_result = result
                        best_score = score
                raise
            score = _score_glossary_result(result)
            if best_score is None or score > best_score:
                best_result = result
                best_score = score
            return result

        try:
            return await retry_async(
                operation,
                model=self.model,
                max_transport_retry_attempts=_transport_retry_budget(self.model),
                should_retry=lambda exc: _should_retry_glossary_request(
                    self.model, exc
                ),
                is_format_retry_error=lambda exc: isinstance(
                    exc, _GlossaryFormatRetry
                ),
            )
        except _GlossaryFormatRetry as exc:
            if best_result is not None:
                return best_result
            return exc.args[0] if exc.args else SubtaskResult(
                response_content=json.dumps(
                    {"entries": [], "issues": []}, ensure_ascii=False
                ),
                input_tokens=0,
                output_tokens=0,
            )

    async def _attempt(
        self, chunk_id: str, source_file: str, text: str, *, attempt_index: int = 0
    ) -> SubtaskResult:
        instruction_prompt = build_prompt(
            self.prompt_preset,
            PromptContext(
                source_language=language_prompt_label(self.source_language),
                target_language=language_prompt_label(self.target_language),
            ),
            thinking=self.model.thinking_prompt_enabled,
        )
        prompt_text = _inject_first_name(
            text, (self.name_injections or {}).get(source_file, "")
        )
        session = self.fake_name_session
        if session is not None:
            prompt_text = session.apply(prompt_text)
        user_prompt = _build_glossary_user_prompt(
            instruction_prompt,
            prompt_text,
            novel_background=self.novel_background,
            target_language=language_prompt_label(self.target_language),
            format_retry=attempt_index > 0,
        )

        reservation = -1
        if self.tpm_limiter is not None:
            estimated = estimate_tokens_from_text(user_prompt)
            reservation = await self.tpm_limiter.reserve(estimated)

        request_model = _with_glossary_soft_timeout(self.model)
        if self.debug_log_dir is not None:
            write_subtask_debug_log(
                self.debug_log_dir,
                chunk_id,
                {
                    "kind": "glossary",
                    "status": "request_started",
                    "system_prompt": "",
                    "instruction_prompt": instruction_prompt,
                    "user_prompt": user_prompt,
                    "timeout_seconds": request_model.timeout_seconds,
                    "attempt_index": attempt_index,
                },
            )

        response = None
        request_started = time.monotonic()
        try:
            response = await self.client.chat(
                ChatRequest(
                    model=request_model,
                    system_prompt="",
                    user_prompt=user_prompt,
                    stream=self.stream,
                    key_pool=self.key_pool,
                    log_label=f"glossary {chunk_id}",
                )
            )
        except BaseException as exc:
            if self.debug_log_dir is not None:
                write_subtask_debug_log(
                    self.debug_log_dir,
                    chunk_id,
                    {
                        "kind": "glossary",
                        "status": "request_failed",
                        "system_prompt": "",
                        "instruction_prompt": instruction_prompt,
                        "user_prompt": user_prompt,
                        "timeout_seconds": request_model.timeout_seconds,
                        "elapsed_seconds": time.monotonic() - request_started,
                        "attempt_index": attempt_index,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            raise
        finally:
            if self.tpm_limiter is not None and reservation >= 0:
                actual = (
                    response.usage.total_tokens if response is not None else 0
                )
                self.tpm_limiter.settle(reservation, actual)
        assert response is not None

        raw_content = response.content
        if session is not None:
            raw_content, _ = session.restore(raw_content)
        decoded = decode_glossary_jsonl(raw_content)
        quality_issues = _glossary_quality_issues(decoded.entries)
        issues = (*decoded.issues, *quality_issues)
        result_payload = {
            "entries": [
                {"src": entry.src, "dst": entry.dst, "info": entry.info}
                for entry in decoded.entries
            ],
            "issues": [
                {"line": issue.line, "reason": issue.reason}
                for issue in issues
            ],
        }

        if self.debug_log_dir is not None:
            write_subtask_debug_log(
                self.debug_log_dir,
                chunk_id,
                {
                    "kind": "glossary",
                    "status": "request_completed",
                    "system_prompt": "",
                    "instruction_prompt": instruction_prompt,
                    "user_prompt": user_prompt,
                    "timeout_seconds": request_model.timeout_seconds,
                    "elapsed_seconds": time.monotonic() - request_started,
                    "attempt_index": attempt_index,
                    "raw_response": response.content,
                    "restored_response": raw_content,
                    "entries": result_payload["entries"],
                    "issues": result_payload["issues"],
                    "usage": response.usage.to_dict(),
                },
            )
        result = SubtaskResult(
            response_content=json.dumps(result_payload, ensure_ascii=False),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        # Decoder produced nothing usable from a non-trivial response —
        # signal retry. ``result`` is attached so the run() outer handler
        # can return it if all attempts fail (instead of silently losing
        # the token usage record).
        if (
            len(decoded.entries) == 0
            and len(issues) >= _FORMAT_RETRY_MIN_ISSUES
        ) or (
            len(decoded.entries) > 0
            and len(issues) > 0
        ):
            raise _GlossaryFormatRetry(result)
        return result


def _with_glossary_soft_timeout(model: ModelConfig) -> ModelConfig:
    if model.timeout_seconds <= _SOFT_TIMEOUT_SECONDS:
        return model
    return replace(model, timeout_seconds=_SOFT_TIMEOUT_SECONDS)


def _should_split_after_transport_timeout(
    model: ModelConfig, exc: LlmRequestError, depth: int
) -> bool:
    if depth >= _HIGH_CONCURRENCY_MAX_SPLIT_DEPTH:
        return False
    if getattr(exc, "code", "") != "llm.transport_error":
        return False
    return "timeout" in str(exc).lower()


def _split_text_in_half(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    if len(lines) >= 2:
        midpoint = len(lines) // 2
        first = "\n".join(lines[:midpoint]).strip()
        second = "\n".join(lines[midpoint:]).strip()
        if first and second:
            return (first, second)
    if len(text) < 2:
        return (text,)
    midpoint = len(text) // 2
    first = text[:midpoint].strip()
    second = text[midpoint:].strip()
    if not first or not second:
        return (text,)
    return (first, second)


def _merge_glossary_results(results: list[SubtaskResult]) -> SubtaskResult:
    entries: list[Mapping[str, object]] = []
    issues: list[Mapping[str, object]] = []
    input_tokens = 0
    output_tokens = 0
    for result in results:
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        try:
            payload = json.loads(result.response_content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        raw_entries = payload.get("entries", [])
        raw_issues = payload.get("issues", [])
        if isinstance(raw_entries, list):
            entries.extend(item for item in raw_entries if isinstance(item, Mapping))
        if isinstance(raw_issues, list):
            issues.extend(item for item in raw_issues if isinstance(item, Mapping))
    return SubtaskResult(
        response_content=json.dumps(
            {"entries": entries, "issues": issues},
            ensure_ascii=False,
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _transport_retry_budget(model: ModelConfig) -> int:
    return _TRANSPORT_RETRY_BUDGET


def decode_glossary_subtask_response(
    response_content: str,
) -> tuple[tuple[GlossaryEntry, ...], tuple[Mapping[str, str], ...]]:
    """Parse the JSON the runner stores in ``Subtask.response_content``.

    Returns ``(entries, issues)``. Used by the orchestrator after the
    executor settles. Returning ``Mapping`` rather than a typed dataclass
    keeps the orchestrator independent of the decoder's internal types.
    """

    if not response_content:
        return (), ()
    try:
        payload = json.loads(response_content)
    except json.JSONDecodeError:
        return (), ()
    if not isinstance(payload, Mapping):
        return (), ()
    raw_entries = payload.get("entries", [])
    raw_issues = payload.get("issues", [])
    entries: list[GlossaryEntry] = []
    if isinstance(raw_entries, list):
        for item in raw_entries:
            if not isinstance(item, Mapping):
                continue
            entries.append(
                GlossaryEntry(
                    src=str(item.get("src", "")),
                    dst=str(item.get("dst", "")),
                    info=str(item.get("info", "")),
                )
            )
    issues: list[Mapping[str, str]] = []
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if isinstance(item, Mapping):
                issues.append(
                    {
                        "line": str(item.get("line", "")),
                        "reason": str(item.get("reason", "")),
                    }
                )
    return tuple(entries), tuple(issues)


def _inject_first_name(text: str, first_name: str) -> str:
    if not first_name:
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith(f"【{first_name}】"):
            return text
        lines[index] = f"【{first_name}】{line}"
        return "\n".join(lines)
    return text


def _build_glossary_user_prompt(
    instruction_prompt: str,
    source_text: str,
    *,
    novel_background: str,
    target_language: str,
    format_retry: bool,
) -> str:
    parts: list[str] = []
    if format_retry:
        parts.append(_FORMAT_RETRY_REMINDER)
    parts.append(instruction_prompt)
    parts.append("[Novel Background]\n" + novel_background)
    parts.append("[Source Text]\n" + source_text)
    parts.append(_output_contract_reminder(target_language))
    return "\n\n".join(part for part in parts if part)


def _glossary_quality_issues(
    entries: tuple[GlossaryEntry, ...]
) -> tuple[DecodeIssue, ...]:
    issues: list[DecodeIssue] = []
    for entry in entries:
        info = entry.info.strip()
        if not info:
            issues.append(
                DecodeIssue(line=entry.src, reason="missing or empty 'type/info'")
            )
    return tuple(issues)


def _score_glossary_result(result: SubtaskResult) -> tuple[int, int, int]:
    entries, issues = decode_glossary_subtask_response(result.response_content)
    complete_entries = sum(
        1 for entry in entries if entry.src and entry.dst and entry.info
    )
    return (complete_entries, len(entries), -len(issues))


__all__ = [
    "GlossarySubtaskRunner",
    "SUBTASK_PAYLOAD_VERSION",
    "decode_glossary_subtask_response",
    "encode_glossary_payload",
]
