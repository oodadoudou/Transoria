"""Glossary review subtask runner."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import json_repair

from transoria.llm.client import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LlmClient,
    LlmRequestError,
    LlmTruncatedResponseError,
)
from transoria.llm.config import ModelConfig
from transoria.llm.retry import _is_format_drift_error, is_transient_llm_error, retry_async
from transoria.llm.usage import TokenUsage, estimate_tokens_from_text
from transoria.prompts import PromptContext, PromptPreset, build_prompt
from transoria.domain import Language, language_prompt_label
from transoria.runtime.executor import SubtaskResult
from transoria.runtime.key_pool import KeyPool
from transoria.runtime.rate_limit import TpmLimiter
from transoria.runtime.subtask import Subtask
from transoria.workflows.debug_log import write_subtask_debug_log

SUBTASK_PAYLOAD_VERSION = 1
SUBTASK_MODE_REVIEW = "review"
SUBTASK_MODE_CHARACTER_CONSISTENCY = "character_consistency"

VALID_ACTIONS: frozenset[str] = frozenset(
    {"keep", "modify", "delete", "category", "modify_category"}
)

# Presets may demand a decision per row, so long replies continue in-conversation.
MAX_CONTINUATIONS = 8
# Keeps the single-request name pass inside common context windows; small glossaries are untouched.
CONSISTENCY_CONTEXT_BUDGET_CHARS = 80_000
UNREADABLE_RETRY_BUDGET = 1
CONTINUE_PROMPT = (
    "Your previous reply was cut off by the output length limit. Continue the "
    "same JSON output exactly where it stopped. Do not repeat items already "
    "written, do not restart the array, and do not add any explanation."
)


@dataclass(frozen=True)
class ReviewDecision:
    row_index: int
    action: str
    suggested_dst: str
    suggested_info: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "row_index": self.row_index,
            "action": self.action,
            "suggested_dst": self.suggested_dst,
            "suggested_info": self.suggested_info,
            "reason": self.reason,
        }


def encode_review_payload(
    *,
    round_index: int,
    batch_index: int,
    rows: tuple[Mapping[str, object], ...],
    novel_background: str,
    mode: str = SUBTASK_MODE_REVIEW,
) -> dict[str, object]:
    return {
        "version": SUBTASK_PAYLOAD_VERSION,
        "mode": mode,
        "round": round_index,
        "batch": batch_index,
        "novel_background": novel_background,
        "rows": list(rows),
    }


def decode_review_response(response_content: str) -> tuple[ReviewDecision, ...]:
    if not response_content:
        return ()
    try:
        payload = json.loads(response_content)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, Mapping):
        return ()
    raw_decisions = payload.get("decisions", [])
    if not isinstance(raw_decisions, list):
        return ()
    decisions: list[ReviewDecision] = []
    for item in raw_decisions:
        if not isinstance(item, Mapping):
            continue
        row_index = _coerce_int(item.get("row_index"))
        if row_index <= 0:
            continue
        action = str(item.get("action", "keep")).strip().lower()
        if action not in VALID_ACTIONS:
            action = "keep"
        decisions.append(
            ReviewDecision(
                row_index=row_index,
                action=action,
                suggested_dst=str(
                    item.get("suggested_dst", item.get("dst", ""))
                ).strip(),
                suggested_info=str(
                    item.get("suggested_info", item.get("info", ""))
                ).strip(),
                reason=str(item.get("reason", "")).strip(),
            )
        )
    return tuple(decisions)


@dataclass(frozen=True)
class GlossaryReviewSubtaskRunner:
    client: LlmClient
    model: ModelConfig
    prompt_preset: PromptPreset
    source_language: Language = Language.KOREAN
    target_language: Language = Language.CHINESE_SIMPLIFIED
    tpm_limiter: TpmLimiter | None = None
    key_pool: KeyPool | None = None
    stream: bool = False
    debug_log_dir: Path | None = None
    transport_retry_attempts: int = 3

    async def run(self, subtask: Subtask) -> SubtaskResult:
        return await retry_async(
            lambda: self._attempt(subtask),
            transport_retry_attempts=self.transport_retry_attempts,
            max_format_retry_attempts=UNREADABLE_RETRY_BUDGET,
            should_retry=lambda exc: isinstance(exc, ReviewReplyUnreadableError)
            or is_transient_llm_error(exc),
            is_format_retry_error=lambda exc: isinstance(
                exc, ReviewReplyUnreadableError
            )
            or _is_format_drift_error(exc),
        )

    async def _attempt(self, subtask: Subtask) -> SubtaskResult:
        payload = subtask.request_payload
        round_index = int(payload.get("round", 1))
        batch_index = int(payload.get("batch", 0))
        rows = payload.get("rows", [])
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"Invalid glossary review payload: {payload!r}")
        mode = str(payload.get("mode", SUBTASK_MODE_REVIEW))

        instruction_prompt = build_prompt(
            self.prompt_preset,
            PromptContext(
                source_language=language_prompt_label(self.source_language),
                target_language=language_prompt_label(self.target_language),
            ),
            thinking=self.model.thinking_prompt_enabled,
        )
        payload_rows = tuple(item for item in rows if isinstance(item, Mapping))
        if mode == SUBTASK_MODE_CHARACTER_CONSISTENCY:
            user_prompt = _build_character_consistency_prompt(
                instruction_prompt=instruction_prompt,
                novel_background=str(payload.get("novel_background", "")),
                rows=payload_rows,
            )
        else:
            user_prompt = _build_user_prompt(
                instruction_prompt=instruction_prompt,
                novel_background=str(payload.get("novel_background", "")),
                round_index=round_index,
                rows=payload_rows,
            )
        log_label = f"glossary review r{round_index} b{batch_index}"
        content, usage, continued = await self._chat_until_complete(
            user_prompt, log_label=log_label
        )
        items = _reply_items(content, continued)
        batch_ids = {_coerce_int(row.get("row_index")) for row in payload_rows}
        decisions = [
            decision
            for decision in _decisions_from_items(items or [])
            if decision.row_index in batch_ids
        ]
        if items is None or (items and not decisions):
            raise ReviewReplyUnreadableError()

        followup_content = ""
        missing_rows = tuple(
            row
            for row in payload_rows
            if _coerce_int(row.get("row_index"))
            not in {decision.row_index for decision in decisions}
        )
        # Only re-ask when the reply lists unchanged rows; presets that return changes only skip this.
        if (
            mode != SUBTASK_MODE_CHARACTER_CONSISTENCY
            and missing_rows
            and any(decision.action == "keep" for decision in decisions)
        ):
            followup_content, followup_usage, followup_decisions = (
                await self._review_missing_rows(
                    instruction_prompt=instruction_prompt,
                    novel_background=str(payload.get("novel_background", "")),
                    round_index=round_index,
                    rows=missing_rows,
                    log_label=f"{log_label} missing",
                )
            )
            usage = usage + followup_usage
            decisions.extend(followup_decisions)

        result_payload = {
            "round": round_index,
            "batch": batch_index,
            "decisions": [decision.to_dict() for decision in decisions],
        }
        if self.debug_log_dir is not None:
            write_subtask_debug_log(
                self.debug_log_dir,
                subtask.id,
                {
                    "kind": "glossary_review",
                    "mode": mode,
                    "instruction_prompt": instruction_prompt,
                    "user_prompt": user_prompt,
                    "raw_response": content,
                    "missing_rows_response": followup_content,
                    "decisions": result_payload["decisions"],
                    "usage": usage.to_dict(),
                },
            )
        return SubtaskResult(
            response_content=json.dumps(result_payload, ensure_ascii=False),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
        )

    async def _review_missing_rows(
        self,
        *,
        instruction_prompt: str,
        novel_background: str,
        round_index: int,
        rows: tuple[Mapping[str, object], ...],
        log_label: str,
    ) -> tuple[str, TokenUsage, list[ReviewDecision]]:
        prompt = _build_user_prompt(
            instruction_prompt=instruction_prompt,
            novel_background=novel_background,
            round_index=round_index,
            rows=rows,
        )
        try:
            content, usage, continued = await self._chat_until_complete(
                prompt, log_label=log_label
            )
        except LlmRequestError:
            # The main reply already stands; unanswered rows keep their current values as before.
            return "", TokenUsage(), []
        wanted = {_coerce_int(row.get("row_index")) for row in rows}
        decisions = [
            decision
            for decision in _decisions_from_items(_reply_items(content, continued) or [])
            if decision.row_index in wanted
        ]
        return content, usage, decisions

    async def _chat_until_complete(
        self, user_prompt: str, *, log_label: str
    ) -> tuple[str, TokenUsage, bool]:
        parts: list[str] = []
        usage = TokenUsage()
        prompt = user_prompt
        history: tuple[ChatMessage, ...] = ()
        decided = 0
        output_cap: int | None = None
        for continuation in range(MAX_CONTINUATIONS + 1):
            label = log_label if continuation == 0 else f"{log_label} continue {continuation}"
            try:
                response = await self._chat_once(
                    prompt, history, log_label=label, output_cap=output_cap
                )
            except LlmTruncatedResponseError as exc:
                parts.append(exc.partial_response)
                usage = usage + exc.usage
                cut_at = exc.usage.output_tokens
                if 0 < cut_at < self.model.max_output_tokens:
                    output_cap = self.model.max_output_tokens
                seen = len(_scan_decision_items("".join(parts)))
                if continuation == MAX_CONTINUATIONS or (
                    continuation > 0 and seen <= decided
                ):
                    raise
                decided = seen
                history = (
                    ChatMessage(role="user", content=user_prompt),
                    ChatMessage(role="assistant", content="".join(parts)),
                )
                prompt = CONTINUE_PROMPT
                continue
            parts.append(response.content)
            return "".join(parts), usage + response.usage, continuation > 0
        raise AssertionError("unreachable")

    async def _chat_once(
        self,
        prompt: str,
        history: tuple[ChatMessage, ...],
        *,
        log_label: str,
        output_cap: int | None = None,
    ) -> ChatResponse:
        if output_cap is None:
            return await self._send(prompt, history, log_label=log_label, output_cap=None)
        try:
            return await self._send(prompt, history, log_label=log_label, output_cap=output_cap)
        except (LlmTruncatedResponseError, ReviewReplyUnreadableError):
            raise
        except LlmRequestError as exc:
            # Providers reject caps above their own maximum; fall back to the provider default.
            if "HTTP 4" not in str(exc):
                raise
            return await self._send(prompt, history, log_label=log_label, output_cap=None)

    async def _send(
        self,
        prompt: str,
        history: tuple[ChatMessage, ...],
        *,
        log_label: str,
        output_cap: int | None,
    ) -> ChatResponse:
        reservation = -1
        if self.tpm_limiter is not None:
            reservation = await self.tpm_limiter.reserve(
                estimate_tokens_from_text(
                    "".join(message.content for message in history) + prompt
                )
            )
        response: ChatResponse | None = None
        try:
            response = await self.client.chat(
                ChatRequest(
                    model=self.model,
                    system_prompt="",
                    user_prompt=prompt,
                    history=history,
                    stream=self.stream,
                    key_pool=self.key_pool,
                    log_label=log_label,
                    max_output_tokens=output_cap,
                )
            )
        finally:
            if self.tpm_limiter is not None and reservation >= 0:
                actual = response.usage.total_tokens if response is not None else 0
                self.tpm_limiter.settle(reservation, actual)
        assert response is not None
        return response


def _build_user_prompt(
    *,
    instruction_prompt: str,
    novel_background: str,
    round_index: int,
    rows: tuple[Mapping[str, object], ...],
) -> str:
    row_payload = [
        {
            "row_index": int(row.get("row_index", 0)),
            "src": str(row.get("src", "")),
            "dst": str(row.get("dst", "")),
            "info": str(row.get("info", "")),
            "frequency": int(row.get("frequency", 0)),
            "tier": str(row.get("tier", "B")),
            "instruction": str(row.get("instruction", "")),
            "history_context": str(row.get("history_context", "")),
            "is_character": bool(row.get("is_character", False)),
            "current_category": str(row.get("current_category", row.get("info", ""))),
            "context": str(row.get("context", "")),
        }
        for row in rows
    ]
    contract = {
        "row_index": 2,
        "action": "keep | modify | delete | category | modify_category",
        "suggested_dst": "required when action changes translation",
        "suggested_info": "required when action changes category",
        "reason": "short reason",
    }
    parts = [
        instruction_prompt,
        f"[Review Round]\n{round_index}",
        "[Novel Background]\n" + (novel_background or "(empty)"),
        (
            "[Review Rules]\n"
            "Each row includes tier/instruction/history_context/current_category. "
            "Tier S means protected lore and must not be deleted. Tier A means "
            "high-frequency: usually important, but clearly generic extraction "
            "noise should be deleted. Tier C means low-frequency: generic words, "
            "verbs, adjectives, and meaningless fragments may be deleted more "
            "aggressively. If history_context exists, keep the previous conclusion "
            "unless there is a clear fatal error. Always review category and return "
            "a normalized category in suggested_info when it should change."
        ),
        "[Glossary Rows]\n" + json.dumps(row_payload, ensure_ascii=False, indent=2),
        (
            "[Output Contract]\n"
            "Output JSON only. Return either a JSON array or an object with a "
            '"decisions" array. Each item must match this shape:\n'
            + json.dumps(contract, ensure_ascii=False)
            + "\nUse keep for unchanged rows. Do not include prose or Markdown."
        ),
    ]
    return "\n\n".join(part for part in parts if part)


def _build_character_consistency_prompt(
    *,
    instruction_prompt: str,
    novel_background: str,
    rows: tuple[Mapping[str, object], ...],
) -> str:
    context_limit = CONSISTENCY_CONTEXT_BUDGET_CHARS // max(1, len(rows))
    row_payload = [
        {
            "row_index": int(row.get("row_index", 0)),
            "src": str(row.get("src", "")),
            "dst": str(row.get("dst", "")),
            "info": str(row.get("info", "")),
            "context": _fit_context(str(row.get("context", "")), context_limit),
        }
        for row in rows
    ]
    contract = {
        "row_index": 2,
        "action": "modify | category | modify_category",
        "suggested_dst": "required when action changes translation",
        "suggested_info": "required when action changes category",
        "reason": "short reason",
    }
    parts = [
        instruction_prompt,
        "[Final Character Consistency Review]",
        "[Novel Background]\n" + (novel_background or "(empty)"),
        (
            "[Rules]\n"
            "This is the final safety pass after all normal glossary review rounds. "
            "Only check character-name consistency across source languages. Korean "
            "names are the main risk: the same Hangul name, spacing variant, honorific "
            "form, nickname, or partial mention may receive different Chinese names. "
            "Also handle Japanese, Chinese, and other supported source languages when "
            "rows clearly refer to the same named person or stable alias. Make Chinese "
            "dst names and character info/category labels consistent. Use category "
            "or modify_category when entries for the same character use conflicting "
            "character labels, including gender, role type, alias type, or another "
            "user-defined category meaning. Choose the label best supported by context "
            "and novel background. Do not merge rows. Do not "
            "delete rows. Do not change src. Do not force unrelated people with the "
            "same surname, generic title, or common word into one name. If uncertain, "
            "leave the row unchanged."
        ),
        "[Character Rows]\n" + json.dumps(row_payload, ensure_ascii=False, indent=2),
        (
            "[Output Contract]\n"
            "Output JSON only. Return either a JSON array or an object with a "
            '"decisions" array. Include only rows whose dst or category must '
            "change; omit unchanged rows. Return an empty array when no row needs "
            "a change. Each item must match:\n"
            + json.dumps(contract, ensure_ascii=False)
        ),
    ]
    return "\n\n".join(part for part in parts if part)


def _fit_context(context: str, limit: int) -> str:
    """Keep whole excerpts while they fit; always keep at least part of the first one."""

    if len(context) <= limit:
        return context
    kept: list[str] = []
    used = 0
    for excerpt in context.split("\n"):
        cost = len(excerpt) + (1 if kept else 0)
        if used + cost > limit:
            break
        kept.append(excerpt)
        used += cost
    if kept:
        return "\n".join(kept)
    return context.split("\n", 1)[0][:limit]


class ReviewReplyUnreadableError(LlmRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Model reply contained no readable JSON decisions for this batch.",
            code="llm.malformed_response",
        )


def _reply_items(content: str, continued: bool) -> list[Mapping[str, object]] | None:
    """Decision objects from a reply; None when nothing readable came back."""

    if continued:
        return _scan_decision_items(content) or None
    return _parse_decision_items(content)


def _parse_decision_items(raw: str) -> list[Mapping[str, object]] | None:
    text = _strip_fence(raw)
    if not text:
        return None
    parsed = _parse_json(text)
    if parsed is None:
        parsed = _parse_json_lines(text)
    if isinstance(parsed, Mapping) and isinstance(parsed.get("decisions"), list):
        parsed = parsed["decisions"]
    if isinstance(parsed, list):
        items = [item for item in parsed if isinstance(item, Mapping)]
        if items or not parsed:
            return items
    return _scan_decision_items(raw) or None


def _scan_decision_items(text: str) -> list[Mapping[str, object]]:
    """Collect every complete decision object, skipping fragments cut at a reply boundary."""

    decoder = json.JSONDecoder()
    items: list[Mapping[str, object]] = []
    index = text.find("{")
    while index >= 0:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, Mapping) and "row_index" in value:
            items.append(value)
        elif isinstance(value, Mapping) and isinstance(value.get("decisions"), list):
            items.extend(item for item in value["decisions"] if isinstance(item, Mapping))
        else:
            index = text.find("{", index + 1)
            continue
        index = text.find("{", end)
    return items


def _decisions_from_items(items: list[object]) -> tuple[ReviewDecision, ...]:
    decisions: list[ReviewDecision] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        row_index = _coerce_int(item.get("row_index"))
        if row_index <= 0:
            continue
        action = str(item.get("action", "keep")).strip().lower()
        if action not in VALID_ACTIONS:
            action = "keep"
        decisions.append(
            ReviewDecision(
                row_index=row_index,
                action=action,
                suggested_dst=str(
                    item.get("suggested_dst", item.get("dst", ""))
                ).strip(),
                suggested_info=str(
                    item.get("suggested_info", item.get("info", ""))
                ).strip(),
                reason=str(item.get("reason", "")).strip(),
            )
        )
    return tuple(decisions)


def _strip_fence(raw: str) -> str:
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", raw, flags=re.DOTALL)
    return match.group(1).strip() if match else raw.strip()


def _parse_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json_repair.loads(text)
        except (TypeError, ValueError):
            return None


def _parse_json_lines(text: str) -> list[object] | None:
    values: list[object] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parsed = _parse_json(stripped)
        if parsed is not None:
            values.append(parsed)
    return values if values else None


def _coerce_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


__all__ = [
    "GlossaryReviewSubtaskRunner",
    "ReviewDecision",
    "decode_review_response",
    "encode_review_payload",
    "SUBTASK_MODE_CHARACTER_CONSISTENCY",
    "SUBTASK_MODE_REVIEW",
]
