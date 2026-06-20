"""Verifies the fake-name roster is applied/restored across the runner."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Mapping

from transoria.domain import Language
from transoria.llm import LlmClient, ModelConfig, ProviderFormat
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask
from transoria.workflows.fake_name import FakeNameRoster
from transoria.workflows.translation import (
    Glossary,
    PreparedSegment,
    TranslationSubtaskRunner,
    build_chunks,
    encode_subtask_payload,
    preprocess_segment,
)


@dataclass
class CapturingTransport:
    seen_user_prompts: list[str] = field(default_factory=list)
    response_content: str = ""

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.seen_user_prompts.append(payload["messages"][-1]["content"])
        body = {
            "choices": [{"message": {"role": "assistant", "content": self.response_content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return TransportResult(200, body)


def _make_subtask(text: str) -> Subtask:
    pre = preprocess_segment(text)
    prepared = PreparedSegment(
        segment_id="0:0",
        original_text=text,
        preprocessed=pre,
    )
    chunk = build_chunks(
        (prepared,),
        chunk_size=1,
        context_line_count=0,
        glossary=Glossary.empty(),
    )[0]
    metadata = [
        {
            "original_text": prepared.original_text,
            "protection_spans": list(prepared.preprocessed.protection.spans),
            "leading_whitespace": prepared.preprocessed.leading_whitespace,
            "trailing_whitespace": prepared.preprocessed.trailing_whitespace,
        }
    ]
    return Subtask(
        id="chunk",
        task_id="t",
        request_payload=encode_subtask_payload(chunk, segment_metadata=metadata),
    )


def _model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="m",
        api_keys=("k",),
    )


def test_runner_masks_rare_chars_in_outgoing_prompt() -> None:
    """User prompt sent to the LLM must contain the placeholder, not the rare char."""

    roster = FakeNameRoster(mapping={"龘": "ZAEZ"})
    transport = CapturingTransport(response_content='{"0":"ZAEZ stays"}')
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        fake_name_roster=roster,
    )

    asyncio.run(runner.run(_make_subtask("rare 龘 char")))

    sent_prompt = transport.seen_user_prompts[0]
    assert "龘" not in sent_prompt
    assert "ZAEZ" in sent_prompt


def test_runner_restores_rare_chars_in_translation() -> None:
    """The model echoes the placeholder; the runner restores 龘 before postprocess."""

    roster = FakeNameRoster(mapping={"龘": "ZAEZ"})
    transport = CapturingTransport(response_content='{"0":"ZAEZ stays"}')
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        fake_name_roster=roster,
    )

    result = asyncio.run(runner.run(_make_subtask("rare 龘 char")))

    payload = json.loads(result.response_content)
    assert "龘" in payload["translations"]["0:0"]
