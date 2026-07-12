from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from transoria.domain import Language
from transoria.llm import (
    LlmClient,
    LlmDegenerateOutputError,
    ModelConfig,
    ProviderFormat,
)
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask
from transoria.workflows.translation import (
    Glossary,
    PreparedSegment,
    TranslationSubtaskRunner,
    build_chunks,
    encode_subtask_payload,
    preprocess_segment,
)


@dataclass
class FlakyStreamingTransport:
    """Fails on first call (transient 503), succeeds on retry.

    Records ``stream`` flag from each call to confirm it's preserved
    through the retry loop.
    """

    successful_content: str = ""
    seen_stream_flags: list[bool] = field(default_factory=list)
    call_count: int = 0

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.call_count += 1
        self.seen_stream_flags.append(bool(payload.get("stream", False)))
        if self.call_count == 1:
            return TransportResult(503, {"error": "service unavailable"})
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": self.successful_content}}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5},
        }
        return TransportResult(200, body)


def _make_subtask() -> Subtask:
    pre = preprocess_segment("hello")
    prepared = PreparedSegment(
        segment_id="0:0", original_text="hello", preprocessed=pre
    )
    chunk = build_chunks(
        (prepared,),
        chunk_size=1,
        context_line_count=0,
        glossary=Glossary.empty(),
    )[0]
    return Subtask(
        id="chunk",
        task_id="t",
        request_payload=encode_subtask_payload(
            chunk,
            segment_metadata=[
                {
                    "original_text": "hello",
                    "protection_spans": [],
                    "leading_whitespace": "",
                    "trailing_whitespace": "",
                }
            ],
        ),
    )


def test_streaming_request_retries_after_transient_5xx_then_succeeds() -> None:
    transport = FlakyStreamingTransport(
        successful_content='{"0":"안녕"}'
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="m",
            api_keys=("k",),
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        stream=True,
        transport_retry_attempts=2,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "안녕"}
    # The stream flag must persist across both attempts; the retry path
    # cannot silently switch a streaming request to non-streaming.
    assert transport.call_count == 2
    assert transport.seen_stream_flags == [True, True]


def test_translation_aborts_degenerate_output_without_transport_retry() -> None:
    @dataclass
    class DegenerateTransport:
        call_count: int = 0

        async def execute(
            self,
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, object],
            timeout: float,
        ) -> TransportResult:
            self.call_count += 1
            return TransportResult(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "我去" * 300}}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 300},
                },
            )

    transport = DegenerateTransport()
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="m",
            api_keys=("k",),
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        stream=True,
        transport_retry_attempts=3,
    )

    with pytest.raises(LlmDegenerateOutputError):
        asyncio.run(runner.run(_make_subtask()))

    assert transport.call_count == 1
