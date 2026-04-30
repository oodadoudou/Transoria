"""Glossary-extraction subtask runner: prompt → LLM → decode → persist.

Unlike translation, the runner does not enforce any output-line-count
constraint: each chunk produces zero or more candidate entries. Decode
issues are recorded in the subtask payload so the orchestrator can surface
them in the run statistics file without losing the rest of the chunk's
output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from transoria.domain import Language
from transoria.llm.client import ChatRequest, LlmClient
from transoria.llm.config import ModelConfig
from transoria.llm.decoders import GlossaryEntry, decode_glossary_jsonl
from transoria.llm.retry import retry_async
from transoria.llm.usage import estimate_tokens_from_text
from transoria.runtime.key_pool import KeyPool
from transoria.runtime.rate_limit import TpmLimiter
from transoria.prompts import PromptContext, PromptPreset, build_prompt
from transoria.runtime.executor import SubtaskResult
from transoria.runtime.subtask import Subtask
from transoria.workflows.debug_log import write_subtask_debug_log
from transoria.workflows.fake_name import FakeNameSession
from transoria.workflows.glossary.chunker import GlossaryChunk


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

    async def run(self, subtask: Subtask) -> SubtaskResult:
        chunk_id, source_file, text = _decode_chunk(subtask.request_payload)
        return await retry_async(
            lambda: self._attempt(chunk_id, source_file, text),
            model=self.model,
        )

    async def _attempt(self, chunk_id: str, source_file: str, text: str) -> SubtaskResult:
        system_prompt = build_prompt(
            self.prompt_preset,
            PromptContext(
                source_language=self.source_language.value,
                target_language=self.target_language.value,
            ),
            thinking=self.model.thinking_enabled,
        )
        prompt_text = _inject_first_name(
            text, (self.name_injections or {}).get(source_file, "")
        )
        session = self.fake_name_session
        if session is not None:
            prompt_text = session.apply(prompt_text)
        user_prompt = "[Source Text]\n" + prompt_text

        reservation = -1
        if self.tpm_limiter is not None:
            estimated = estimate_tokens_from_text(system_prompt) + estimate_tokens_from_text(
                user_prompt
            )
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
                )
            )
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
        result_payload = {
            "entries": [
                {"src": entry.src, "dst": entry.dst, "info": entry.info}
                for entry in decoded.entries
            ],
            "issues": [
                {"line": issue.line, "reason": issue.reason}
                for issue in decoded.issues
            ],
        }

        if self.debug_log_dir is not None:
            write_subtask_debug_log(
                self.debug_log_dir,
                chunk_id,
                {
                    "kind": "glossary",
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "raw_response": response.content,
                    "restored_response": raw_content,
                    "entries": result_payload["entries"],
                    "issues": result_payload["issues"],
                    "usage": response.usage.to_dict(),
                },
            )
        return SubtaskResult(
            response_content=json.dumps(result_payload, ensure_ascii=False),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


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


__all__ = [
    "GlossarySubtaskRunner",
    "SUBTASK_PAYLOAD_VERSION",
    "decode_glossary_subtask_response",
    "encode_glossary_payload",
]
