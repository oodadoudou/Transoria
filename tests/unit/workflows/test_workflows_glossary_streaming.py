from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from transoria.domain import Language
from transoria.llm import LlmClient, ModelConfig, ProviderFormat
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask
from transoria.workflows.glossary import (
    GlossaryChunk,
    GlossarySubtaskRunner,
    encode_glossary_payload,
)


@dataclass
class StreamFlagTransport:
    captured: list[bool] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.captured.append(bool(payload.get("stream", False)))
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"src":"신해범","dst":"申海范","type":"Male Name"}',
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return TransportResult(200, body)


def _make_subtask() -> Subtask:
    chunk = GlossaryChunk(
        chunk_id="chunk", source_file=Path("/in/Sample.txt"), text="신해범 walks"
    )
    return Subtask(
        id=chunk.chunk_id, task_id="t", request_payload=encode_glossary_payload(chunk)
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


def test_glossary_runner_streams_when_stream_flag_is_true() -> None:
    transport = StreamFlagTransport()
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        stream=True,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.captured == [True]


def test_glossary_runner_does_not_stream_by_default() -> None:
    transport = StreamFlagTransport()
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.captured == [False]
