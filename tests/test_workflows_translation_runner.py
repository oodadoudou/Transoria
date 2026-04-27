from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from transoria.domain import Language
from transoria.llm import (
    LlmClient,
    LlmRequestError,
    ModelConfig,
    ProviderFormat,
    ThinkingLevel,
)
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask
from transoria.workflows.translation import (
    Glossary,
    PreparedSegment,
    PreprocessedSegment,
    ProtectionMap,
    ReplacementRule,
    TextPreserveRule,
    TranslationSubtaskRunner,
    build_chunks,
    encode_subtask_payload,
    preprocess_segment,
)


@dataclass
class FakeTransport:
    responses: list[TransportResult] = field(default_factory=list)
    last_request: dict[str, object] | None = field(default=None, init=False)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.last_request = {
            "url": url,
            "headers": dict(headers),
            "payload": dict(payload),
            "timeout": timeout,
        }
        return self.responses.pop(0)


def _model(thinking: ThinkingLevel = ThinkingLevel.OFF) -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("key",),
        thinking_level=thinking,
    )


def _ok_body(content: str) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 30},
    }


def _make_subtask(
    *,
    sources: tuple[str, ...] = ("hello", "world"),
    text_preserve_rules: tuple[TextPreserveRule, ...] = (),
    pre_replacements: tuple[ReplacementRule, ...] = (),
) -> Subtask:
    prepared = []
    for offset, text in enumerate(sources):
        pre = preprocess_segment(
            text,
            text_preserve_rules=text_preserve_rules,
            pre_replacements=pre_replacements,
        )
        prepared.append(
            PreparedSegment(
                segment_id=f"0:{offset}",
                original_text=text,
                preprocessed=pre,
            )
        )

    chunks = build_chunks(
        tuple(prepared),
        chunk_size=len(prepared),
        context_line_count=0,
        glossary=Glossary.empty(),
    )
    chunk = chunks[0]
    metadata = [
        {
            "original_text": item.original_text,
            "protection_spans": list(item.preprocessed.protection.spans),
            "leading_whitespace": item.preprocessed.leading_whitespace,
            "trailing_whitespace": item.preprocessed.trailing_whitespace,
        }
        for item in prepared
    ]
    payload = encode_subtask_payload(chunk, segment_metadata=metadata)
    return Subtask(id="chunk-00000", task_id="t1", request_payload=payload)


def test_runner_returns_translations_keyed_by_segment_id() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"안녕"}\n{"1":"세상"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "안녕", "0:1": "세상"}
    assert payload["low_confidence"] == []
    assert result.input_tokens == 50
    assert result.output_tokens == 30


def test_runner_assembles_translate_section_in_user_prompt() -> None:
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body('{"0":"x"}\n{"1":"y"}\n'))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    asyncio.run(runner.run(_make_subtask()))

    messages = transport.last_request["payload"]["messages"]
    user_message = messages[-1]["content"]
    assert "[Translate]" in user_message
    assert '{"0": "hello"}' in user_message
    assert '{"1": "world"}' in user_message


def test_runner_includes_thinking_block_in_system_prompt_when_enabled() -> None:
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body('{"0":"x"}\n{"1":"y"}\n'))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(thinking=ThinkingLevel.HIGH),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    asyncio.run(runner.run(_make_subtask()))

    messages = transport.last_request["payload"]["messages"]
    system_message = messages[0]["content"]
    assert "<why>" in system_message
    # Provider-side thinking flag is set by the LLM client too.
    assert transport.last_request["payload"]["thinking"] == {
        "type": "enabled",
        "effort": "high",
    }


def test_runner_raises_when_returned_line_count_does_not_match() -> None:
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body('{"0":"only one"}\n'))]
    )
    no_retry_model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("k",),
        retry_attempts=0,
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=no_retry_model,
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    with pytest.raises(LlmRequestError, match="line count mismatch"):
        asyncio.run(runner.run(_make_subtask()))


def test_runner_restores_protected_spans_in_translation() -> None:
    rules = (TextPreserveRule(pattern=r"\{\{[A-Z_]+\}\}"),)

    # The model echoes back the sentinel; the runner restores the original.
    pre = preprocess_segment("Hello {{NAME}}!", text_preserve_rules=rules)
    sentinel_in_text = pre.prompt_text  # contains the sentinel
    response_content = json.dumps({"0": sentinel_in_text}, ensure_ascii=False)
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body(response_content))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )
    subtask = _make_subtask(
        sources=("Hello {{NAME}}!",), text_preserve_rules=rules
    )

    result = asyncio.run(runner.run(subtask))

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"].endswith("{{NAME}}!")


def test_runner_retries_on_line_count_mismatch_then_succeeds() -> None:
    """Transient mismatch on first attempt → retry → success on second."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"only one"}\n')),
            TransportResult(200, _ok_body('{"0":"안녕"}\n{"1":"세상"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="model-x",
            api_keys=("k",),
            retry_attempts=1,
            retry_initial_backoff_seconds=0.0,
            retry_max_backoff_seconds=0.0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "안녕", "0:1": "세상"}


def test_runner_raises_after_exhausting_retries_on_persistent_mismatch() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"x"}\n')),
            TransportResult(200, _ok_body('{"0":"x"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="model-x",
            api_keys=("k",),
            retry_attempts=1,
            retry_initial_backoff_seconds=0.0,
            retry_max_backoff_seconds=0.0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    with pytest.raises(LlmRequestError, match="line count mismatch"):
        asyncio.run(runner.run(_make_subtask()))


def test_runner_applies_post_replacements() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"申海范 walks"}\n{"1":"world"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
        post_replacements=(
            ReplacementRule(src="申海范", dst="申海凡", case_sensitive=True),
        ),
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "申海凡 walks"
    assert payload["translations"]["0:1"] == "world"
