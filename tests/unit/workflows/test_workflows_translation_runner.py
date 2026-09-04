from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import Mapping

import pytest

from transoria.domain import Language, language_prompt_label
from transoria.llm import (
    LlmClient,
    ModelConfig,
    ProviderFormat,
    ThinkingLevel,
)
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, PromptPreset, default_preset
from transoria.runtime import Subtask, SubtaskFailedWithResult
from transoria.workflows.translation import (
    Glossary,
    GlossaryEntry,
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
from transoria.workflows.translation.runner import _decode_subtask_payload
from transoria.workflows.translation.segment_state import (
    PRESERVED_CANDIDATE_SEGMENTS_KEY,
)


@dataclass
class FakeTransport:
    responses: list[TransportResult] = field(default_factory=list)
    requests: list[dict[str, object]] = field(default_factory=list)
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
        self.requests.append(self.last_request)
        return self.responses.pop(0)


@dataclass
class FlakyTransport:
    outcomes: list[TransportResult | BaseException]
    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout": timeout,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@dataclass
class TrackingTransport(FakeTransport):
    active_solo_requests: int = 0
    max_solo_requests: int = 0

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        messages = payload.get("messages", [])
        content = ""
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, Mapping):
                content = str(last.get("content", ""))
        translate_lines = [
            line for line in content.splitlines() if line.startswith("{")
        ]
        is_solo_retry = len(translate_lines) == 1
        if is_solo_retry:
            self.active_solo_requests += 1
            self.max_solo_requests = max(
                self.max_solo_requests, self.active_solo_requests
            )
            await asyncio.sleep(0.01)
        try:
            return await super().execute(url, headers, payload, timeout)
        finally:
            if is_solo_retry:
                self.active_solo_requests -= 1


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
    glossary: Glossary | None = None,
    text_preserve_rules: tuple[TextPreserveRule, ...] = (),
    pre_replacements: tuple[ReplacementRule, ...] = (),
    attempt_count: int = 0,
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
        glossary=glossary or Glossary.empty(),
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
    return Subtask(
        id="chunk-00000",
        task_id="t1",
        request_payload=payload,
        attempt_count=attempt_count,
    )


def _make_subtask_with_context(
    *,
    context: tuple[str, ...],
    sources: tuple[str, ...],
) -> Subtask:
    prepared = []
    for offset, text in enumerate(sources):
        pre = preprocess_segment(
            text,
            text_preserve_rules=(),
            pre_replacements=(),
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
    chunk = type(chunk)(
        segments=chunk.segments,
        context_lines=context,
        glossary_entries=chunk.glossary_entries,
    )
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


def test_runner_prompt_names_traditional_chinese_explicitly() -> None:
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"世界"}\n'))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_TRADITIONAL,
    )

    asyncio.run(runner.run(_make_subtask(sources=("안녕", "세계"))))

    system_message = transport.last_request["payload"]["messages"][0]["content"]
    assert "Traditional Chinese (繁體中文)" in system_message


def test_runner_keeps_system_prompt_byte_stable_across_chunks() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"一"}\n{"1":"二"}\n')),
            TransportResult(200, _ok_body('{"0":"三"}\n{"1":"四"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask(sources=("first chunk", "alpha"))))
    asyncio.run(runner.run(_make_subtask(sources=("second chunk", "beta"))))

    first_messages = transport.requests[0]["payload"]["messages"]
    second_messages = transport.requests[1]["payload"]["messages"]
    first_system = first_messages[0]["content"]
    second_system = second_messages[0]["content"]
    first_user = first_messages[-1]["content"]
    second_user = second_messages[-1]["content"]
    assert first_system == second_system
    assert first_user != second_user
    assert "first chunk" not in first_system
    assert "second chunk" not in second_system
    assert "first chunk" in first_user
    assert "second chunk" in second_user


def test_runner_normalizes_simplified_output_to_traditional() -> None:
    pytest.importorskip("opencc")
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"他说无法拒绝"}\n{"1":"汉语网络后台"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_TRADITIONAL,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=("안녕", "세계"))))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "他說無法拒絕",
        "0:1": "漢語網絡後臺",
    }


def test_runner_preserves_traditional_output_when_target_is_simplified() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"回乾說無法拒絕"}\n{"1":"漢語網絡後臺"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=("안녕", "세계"))))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "回乾說無法拒絕",
        "0:1": "漢語網絡後臺",
    }


def test_runner_adds_runtime_output_contract_for_empty_suffix_prompt() -> None:
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body('{"0":"x"}\n{"1":"y"}\n'))]
    )
    custom_prompt = PromptPreset(
        id="custom",
        name="Custom",
        kind=PromptKind.TRANSLATION,
        system_prompt="Translate faithfully.",
        suffix_prompt="",
        thinking_prompt="",
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=custom_prompt,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    asyncio.run(runner.run(_make_subtask()))

    messages = transport.last_request["payload"]["messages"]
    system_message = messages[0]["content"]
    user_message = messages[-1]["content"]
    # Format contract lives in the system message (Layer 2): one-time
    # injection per session, not duplicated in every user message.
    assert "[Output transport — runtime protocol]" in system_message
    assert "JSONLINE" in system_message
    assert '{"<INDEX>":"<translated text>"}' in system_message
    # The hint stays scoped to transport — no style/voice/extraction.
    assert "Translate faithfully." in system_message
    # User message now carries only data; no per-request boilerplate.
    assert "Output JSONLINE only" not in user_message
    assert user_message.startswith("[Translate]")


def test_runner_includes_thinking_block_in_system_prompt_when_enabled() -> None:
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body('{"0":"x"}\n{"1":"y"}\n'))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(thinking=ThinkingLevel.HIGH),
        prompt_preset=PromptPreset(
            id="thinking",
            name="Thinking",
            kind=PromptKind.TRANSLATION,
            system_prompt="Translate.",
            thinking_prompt="<why>\nthink first\n</why>",
        ),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    asyncio.run(runner.run(_make_subtask()))

    messages = transport.last_request["payload"]["messages"]
    system_message = messages[0]["content"]
    assert "<why>" not in system_message
    assert "Before answering" in system_message
    # Provider-side thinking flag is set by the LLM client too. We
    # intentionally don't pass ``effort`` — the provider's default
    # budget is used so translation cost doesn't blow up on HIGH tier.
    assert transport.last_request["payload"]["thinking"] == {
        "type": "enabled",
    }


def test_runner_falls_back_when_returned_line_count_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No partial-accept re-ask: the single mismatched response must fall
    # straight through to source fallback + proofreading flag.
    monkeypatch.setattr(
        "transoria.workflows.translation.runner._PARTIAL_ACCEPT_MAX_RETRIES", 0
    )
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
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=no_retry_model,
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {"0:0": "only one", "0:1": "world"}
    assert payload["accepted_overrides"] == ["0:0"]
    assert payload["low_confidence"] == [
        {
            "segment_id": "0:1",
            "reasons": ["line_count_mismatch_after_max_retries"],
            "tags": ["source_residue"],
        }
    ]


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
    """First response is short of one index; partial-accept keeps the
    parsed line and the retry call asks for the missing index only."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"안녕"}\n')),
            TransportResult(200, _ok_body('{"1":"세상"}\n')),
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
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "안녕", "0:1": "세상"}
    assert len(transport.requests) == 2
    second_user_message = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert second_user_message.startswith("FORMAT RETRY:")


def test_runner_keeps_explicit_complete_rows_from_length_truncation() -> None:
    sources = tuple(f"source {idx}" for idx in range(5))
    truncated = (
        "```jsonline\n"
        '{"0":"译文 0"}\n'
        '{"1":"冲突版本 A"}\n'
        '{"1":"冲突版本 B"}\n'
        '{"2":"译文 2"}\n'
        '{"99":"越界内容"}\n'
        '{"3":"被截断的尾行'
    )
    first_body = _ok_body(truncated)
    first_body["choices"][0]["finish_reason"] = "length"  # type: ignore[index]
    transport = FakeTransport(
        responses=[
            TransportResult(200, first_body),
            TransportResult(
                200,
                _ok_body(
                    '{"1":"译文 1"}\n'
                    '{"3":"译文 3"}\n'
                    '{"4":"译文 4"}\n'
                ),
            ),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        f"0:{idx}": f"译文 {idx}" for idx in range(5)
    }
    assert len(transport.requests) == 2
    retry_prompt = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "source 0" not in retry_prompt
    assert "source 2" not in retry_prompt
    assert "source 1" in retry_prompt
    assert "source 3" in retry_prompt
    assert "source 4" in retry_prompt
    assert result.input_tokens == 100
    assert result.output_tokens == 60


def test_runner_accumulates_rows_across_repeated_length_truncation() -> None:
    first_body = _ok_body('{"0":"译文 0"}\n{"1":"截断')
    first_body["choices"][0]["finish_reason"] = "length"  # type: ignore[index]
    second_body = _ok_body('{"1":"译文 1"}\n{"2":"截断')
    second_body["choices"][0]["finish_reason"] = "max_tokens"  # type: ignore[index]
    transport = FakeTransport(
        responses=[
            TransportResult(200, first_body),
            TransportResult(200, second_body),
            TransportResult(200, _ok_body('{"2":"译文 2"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask(sources=("source 0", "source 1", "source 2"))
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "译文 0",
        "0:1": "译文 1",
        "0:2": "译文 2",
    }
    assert len(transport.requests) == 3


def test_runner_keeps_complete_rows_before_degenerate_tail() -> None:
    degenerate = '{"0":"译文 0"}\n{"1":"译文 1"}\n{"2":"' + ("哈" * 600)
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body(degenerate))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("source 0", "source 1")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "译文 0", "0:1": "译文 1"}
    assert len(transport.requests) == 1


def test_runner_retries_only_missing_rows_after_degenerate_tail_with_context() -> None:
    degenerate = '{"0":"译文 0"}\n{"1":"' + ("哈" * 600)
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(degenerate)),
            TransportResult(200, _ok_body('{"1":"译文 1"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask_with_context(
                context=("previous context",),
                sources=("source 0", "source 1"),
            )
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "译文 0", "0:1": "译文 1"}
    first_prompt = transport.requests[0]["payload"]["messages"][-1]["content"]
    retry_prompt = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "previous context" not in first_prompt
    assert "previous context" in retry_prompt
    assert "source 0" not in retry_prompt
    assert "source 1" in retry_prompt


def test_runner_failed_degenerate_chunk_preserves_accepted_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "transoria.workflows.translation.runner._PARTIAL_ACCEPT_MAX_RETRIES", 0
    )
    degenerate = '{"0":"译文 0"}\n{"1":"' + ("哈" * 600)
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body(degenerate))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(
            runner.run(_make_subtask(sources=("source 0", "source 1")))
        )

    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {"0:0": "译文 0", "0:1": "source 1"}
    assert payload["accepted_overrides"] == ["0:0"]
    assert payload["low_confidence"] == [
        {
            "segment_id": "0:1",
            "reasons": ["line_count_mismatch_after_max_retries"],
            "tags": ["source_residue"],
        }
    ]


def test_runner_retries_dense_prefix_response_instead_of_shifting_lines() -> None:
    sources = tuple(f"source {idx}" for idx in range(8))
    first_response = "\n".join(
        f'{{"{idx}":"shifted {idx + 1}"}}' for idx in range(7)
    )
    second_response = "\n".join(
        f'{{"{idx}":"정답 {idx}"}}' for idx in range(8)
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(first_response + "\n")),
            TransportResult(200, _ok_body(second_response + "\n")),
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
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        f"0:{idx}": f"정답 {idx}" for idx in range(8)
    }
    assert len(transport.requests) == 2
    second_user_message = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "source 0" in second_user_message
    assert "source 7" in second_user_message


def test_runner_keeps_complete_prefix_when_dense_response_is_truncated() -> None:
    sources = tuple(f"source {idx}" for idx in range(8))
    complete_prefix = "\n".join(
        f'{{"{idx}":"정답 {idx}"}}' for idx in range(6)
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(complete_prefix + '\n{"6":"잘린')),
            TransportResult(200, _ok_body('{"6":"꼬리 6"}\n{"7":"꼬리 7"}\n')),
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
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        **{f"0:{idx}": f"정답 {idx}" for idx in range(6)},
        "0:6": "꼬리 6",
        "0:7": "꼬리 7",
    }
    assert len(transport.requests) == 2
    second_user_message = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "source 0" not in second_user_message
    assert "source 6" in second_user_message
    assert "source 7" in second_user_message


def test_runner_falls_back_pending_lines_when_partial_retry_transport_exhausts() -> None:
    transport = FlakyTransport(
        outcomes=[
            TransportResult(200, _ok_body('{"0":"你好"}\n')),
            RuntimeError("ConnectError"),
            RuntimeError("ConnectError"),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好", "0:1": "친구입니다"}
    assert payload["low_confidence"] == [
        {
            "segment_id": "0:1",
            "reasons": ["partial_retry_transient_failed"],
            "tags": ["source_residue"],
        }
    ]
    assert len(transport.requests) == 3


def test_runner_retries_high_concurrency_batch_read_error_once() -> None:
    transport = FlakyTransport(
        outcomes=[
            RuntimeError("ReadError"),
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"世界"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=replace(
            _fast_retry_model(),
            concurrency_limit=60,
            timeout_seconds=600.0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好", "0:1": "世界"}
    assert len(transport.requests) == 2
    assert [request["timeout"] for request in transport.requests] == [360.0, 360.0]


def test_runner_caps_batch_transport_retry_budget() -> None:
    transport = FlakyTransport(
        outcomes=[
            RuntimeError("ReadError"),
            RuntimeError("ReadError"),
            RuntimeError("ReadError"),
            RuntimeError("ReadError"),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=replace(
            _fast_retry_model(),
            concurrency_limit=5,
            timeout_seconds=600.0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(Exception) as caught:
        asyncio.run(runner.run(_make_subtask(sources=("안녕하세요", "친구입니다"))))

    assert "ReadError" in str(caught.value)
    assert len(transport.requests) == 4
    assert [request["timeout"] for request in transport.requests] == [
        600.0,
        600.0,
        600.0,
        600.0,
    ]


def test_runner_does_not_retry_high_concurrency_batch_timeout() -> None:
    transport = FlakyTransport(outcomes=[RuntimeError("ReadTimeout")])
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=replace(
            _fast_retry_model(),
            concurrency_limit=60,
            timeout_seconds=600.0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(Exception) as caught:
        asyncio.run(runner.run(_make_subtask(sources=("안녕하세요", "친구입니다"))))

    assert "ReadTimeout" in str(caught.value)
    assert len(transport.requests) == 1
    assert transport.requests[0]["timeout"] == 360.0


def test_runner_retries_prose_response_with_format_reminder() -> None:
    sources = (
        "오신(娛神) 5권 (완결)",
        "판권",
        "6. <신원(伸寃)> 中",
        "6. <신원(伸寃)> 下",
        "[각주 모음]",
    )
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    "《娱神》第五卷完结\n"
                    "版权页\n"
                    "第六章 上\n"
                    "第六章 下\n"
                    "脚注汇总\n"
                ),
            ),
            TransportResult(
                200,
                _ok_body(
                    '{"0":"《娱神》第五卷（完结）"}\n'
                    '{"1":"版权"}\n'
                    '{"2":"6. <伸冤> 中"}\n'
                    '{"3":"6. <伸冤> 下"}\n'
                    '{"4":"[脚注汇总]"}\n'
                ),
            ),
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
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "《娱神》第五卷（完结）",
        "0:1": "版权",
        "0:2": "6. <伸冤> 中",
        "0:3": "6. <伸冤> 下",
        "0:4": "[脚注汇总]",
    }
    assert len(transport.requests) == 2
    first_user_message = transport.requests[0]["payload"]["messages"][-1]["content"]
    retry_user_message = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert first_user_message.startswith("[Translate]\n```jsonline\n")
    assert retry_user_message.startswith("FORMAT RETRY:")
    assert "[Translate]\n```jsonline\n" in retry_user_message


def test_runner_falls_back_after_exhausting_retries_on_persistent_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retries that keep returning empty / parseless content cannot be
    rescued by the positional zip path (zero candidates can't map to
    any expected segment), so the runner pushes unresolved lines to
    proofreading."""

    # One partial-accept re-ask, then fall back: initial + one retry.
    monkeypatch.setattr(
        "transoria.workflows.translation.runner._PARTIAL_ACCEPT_MAX_RETRIES", 1
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"x"}\n')),
            TransportResult(200, _ok_body("")),
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
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {"0:0": "x", "0:1": "world"}
    assert payload["accepted_overrides"] == ["0:0"]
    assert payload["low_confidence"] == [
        {
            "segment_id": "0:1",
            "reasons": ["line_count_mismatch_after_max_retries"],
            "tags": ["source_residue"],
        }
    ]


def test_runner_partial_accept_recovers_on_second_call() -> None:
    """Call 1 returns the first index only; call 2 fills the missing one."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"안녕"}\n')),
            TransportResult(200, _ok_body('{"1":"세상"}\n')),
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
    assert len(transport.requests) == 2


def test_runner_partial_accept_handles_extras_in_retry_response() -> None:
    """Call 2 echoes both indices but only the missing one is needed;
    extras are dropped, the already-accumulated translation is kept."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"안녕"}\n')),
            TransportResult(
                200, _ok_body('{"0":"new"}\n{"1":"세상"}\n')
            ),
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
    # Index 0 keeps its original "안녕" — the retry call wasn't asking
    # for it, so the late "new" is treated as a stale extra.
    assert payload["translations"] == {"0:0": "안녕", "0:1": "세상"}


def test_runner_partial_accept_falls_back_after_retry_attempts_exhausted() -> None:
    """Every call drops the same index; runner keeps accepted lines
    and marks the unresolved line for proofreading after the configured
    retry budget runs out."""

    sources = ("a", "b", "c", "d")
    # All three responses miss index 3 but contain something parseable.
    persistent = TransportResult(
        200, _ok_body('{"0":"x"}\n{"1":"y"}\n{"2":"z"}\n')
    )
    transport = FakeTransport(responses=[persistent, persistent, persistent])
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="model-x",
            api_keys=("k",),
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(runner.run(_make_subtask(sources=sources)))
    # 1 initial + 2 partial retries; positional rescue rejects JSON-shaped
    # candidates, so no silent recovery.
    assert len(transport.requests) == 3
    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {
        "0:0": "x",
        "0:1": "y",
        "0:2": "z",
        "0:3": "d",
    }
    assert payload["accepted_overrides"] == ["0:0", "0:1", "0:2"]
    assert payload["low_confidence"] == [
        {
            "segment_id": "0:3",
            "reasons": ["line_count_mismatch_after_max_retries"],
            "tags": ["source_residue"],
        }
    ]


def test_runner_partial_accept_drops_extras_silently() -> None:
    """Single response with indices outside the expected set still
    counts as full success — extras are silently dropped."""

    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    '{"0":"a"}\n{"1":"b"}\n'
                    '{"4":"x"}\n{"5":"y"}\n'
                ),
            )
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
    assert payload["translations"] == {"0:0": "a", "0:1": "b"}
    assert len(transport.requests) == 1


def test_runner_partial_accept_uses_smaller_user_prompt_on_retry() -> None:
    """The retry call's user prompt only contains the missing source
    line in its [Translate] block, not the already-accumulated ones."""

    sources = ("aaa", "bbb", "ccc", "ddd")
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body('{"0":"a"}\n{"1":"b"}\n{"2":"c"}\n'),
            ),
            TransportResult(200, _ok_body('{"3":"d"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "a",
        "0:1": "b",
        "0:2": "c",
        "0:3": "d",
    }
    assert len(transport.requests) == 2
    second_user_message = transport.requests[1]["payload"]["messages"][-1]["content"]
    # Retry banner present and only the missing source line is in the
    # JSONL block — first three sources do not appear.
    assert second_user_message.startswith("FORMAT RETRY:")
    assert '"3": "ddd"' in second_user_message or '"3":"ddd"' in second_user_message
    assert "aaa" not in second_user_message
    assert "bbb" not in second_user_message
    assert "ccc" not in second_user_message


def test_runner_partial_accept_filters_glossary_to_pending_sources() -> None:
    sources = ("aaa Alpha", "bbb CODE-42 Word", "ccc Gamma")
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="Alpha", dst="阿尔法"),
            GlossaryEntry(src=r"CODE-\d+", dst="编号", regex=True),
            GlossaryEntry(src="Word", dst="词", case_sensitive=True),
            GlossaryEntry(src="Gamma", dst="伽马"),
        )
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"a"}\n{"2":"c"}\n')),
            TransportResult(200, _ok_body('{"1":"b"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=sources, glossary=glossary))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "a", "0:1": "b", "0:2": "c"}
    retry_user_message = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "[Glossary]" in retry_user_message
    assert r"CODE-\d+ -> 编号" in retry_user_message
    assert "Word -> 词" in retry_user_message
    assert "Alpha -> 阿尔法" not in retry_user_message
    assert "Gamma -> 伽马" not in retry_user_message


def test_runner_skips_retry_loop_when_all_segments_pass_confidence() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"朋友"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好", "0:1": "朋友"}
    assert payload["low_confidence"] == []
    assert len(transport.responses) == 0


def test_runner_retries_only_low_confidence_segment_and_keeps_good_one() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"세계"}\n')),
            TransportResult(200, _ok_body('{"1":"世界"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好", "0:1": "世界"}
    assert payload["low_confidence"] == []
    assert len(transport.responses) == 0
    retry_prompt = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "QUALITY RETRY" in retry_prompt
    assert language_prompt_label(Language.CHINESE_SIMPLIFIED) in retry_prompt


def test_runner_batches_many_low_confidence_rows_before_solo_retry() -> None:
    sources = tuple(f"안녕하세요 친구입니다 정말 긴 문장 {idx}" for idx in range(4))
    initial = "\n".join(f'{{"{idx}":"嗯{idx}"}}' for idx in range(4))
    rescue = "\n".join(
        f'{{"{idx}":"这是完整译文第{idx}行，内容已经充分展开。"}}'
        for idx in range(4)
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(initial + "\n")),
            TransportResult(200, _ok_body(rescue + "\n")),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        min_length_ratio=0.5,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["low_confidence"] == []
    assert len(payload["translations"]) == 4
    assert len(transport.requests) == 2
    retry_prompt = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "QUALITY RETRY" in retry_prompt
    assert language_prompt_label(Language.CHINESE_SIMPLIFIED) in retry_prompt
    for idx, source in enumerate(sources):
        assert f'"{idx}": "' in retry_prompt
        assert source in retry_prompt


def test_runner_retries_high_concurrency_solo_read_error_once() -> None:
    transport = FlakyTransport(
        outcomes=[
            TransportResult(200, _ok_body('{"0":"안녕하세요"}\n')),
            RuntimeError("ReadError"),
            TransportResult(200, _ok_body('{"0":"你好"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=replace(_fast_retry_model(), concurrency_limit=60, timeout_seconds=600.0),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=("안녕하세요",))))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好"}
    assert payload["low_confidence"] == []
    assert len(transport.requests) == 3
    assert [request["timeout"] for request in transport.requests] == [
        360.0,
        60.0,
        60.0,
    ]


def test_runner_limits_concurrent_low_confidence_solo_retries() -> None:
    transport = TrackingTransport(
        responses=[
            TransportResult(
                200,
                _ok_body('{"0":"안녕하세요"}\n{"1":"친구입니다"}\n'),
            ),
            TransportResult(
                200,
                _ok_body('{"0":"안녕하세요"}\n{"1":"친구입니다"}\n'),
            ),
            TransportResult(200, _ok_body('{"0":"你好"}\n')),
            TransportResult(200, _ok_body('{"0":"朋友"}\n')),
            TransportResult(200, _ok_body('{"0":"你好"}\n')),
            TransportResult(200, _ok_body('{"0":"朋友"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
        solo_retry_limiter=asyncio.Semaphore(1),
    )

    async def run_pair() -> None:
        await asyncio.gather(
            runner.run(_make_subtask(sources=("안녕하세요", "친구입니다"))),
            runner.run(_make_subtask(sources=("안녕하세요", "친구입니다"))),
        )

    asyncio.run(run_pair())

    assert transport.max_solo_requests == 1
    assert len(transport.responses) == 0


def test_runner_falls_back_to_source_when_residue_persists() -> None:
    """When the model's last attempt still contains source-language
    residue (e.g. Korean characters in the output), source-passthrough
    is used: a Chinese-shaped string mostly made of Korean letters is
    more confusing than the raw source line. A 'source_residue' tag
    flags it for the proofreading page."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"세계"}\n')),
            TransportResult(200, _ok_body('{"1":"세계"}\n')),
            TransportResult(200, _ok_body('{"1":"세계"}\n')),
            TransportResult(200, _ok_body('{"1":"세계"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "你好"
    # Source-passthrough because the failing retry "세계" is still Korean.
    assert payload["translations"]["0:1"] == "친구입니다"
    assert len(payload["low_confidence"]) == 1
    flagged = payload["low_confidence"][0]
    assert flagged["segment_id"] == "0:1"
    assert "fell_back_to_source_after_max_retries" in flagged["reasons"]
    assert "source_residue" in flagged.get("tags", [])
    assert len(transport.responses) == 2


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("올 어바웃 에단 카터.", "All About Ethan Carter."),
        ("“해피 버스데이 투 유.”", "“happy birthday to you.”"),
        ("“아이엠 글래드 투 씨유.”", "“I'm glad to see you.”"),
    ],
)
def test_runner_stages_korean_latin_candidate_for_review(
    source: str, candidate: str,
) -> None:
    reply = json.dumps({"0": candidate}, ensure_ascii=False)
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(reply)),
            TransportResult(200, _ok_body(reply)),
            TransportResult(200, _ok_body(reply)),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
        preserve_korean_latin_review_candidates=True,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=(source,)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == candidate
    assert payload[PRESERVED_CANDIDATE_SEGMENTS_KEY] == ["0:0"]
    flagged = payload["low_confidence"][0]
    assert "force_accepted_after_max_retries" in flagged["reasons"]
    assert "fell_back_to_source_after_max_retries" not in flagged["reasons"]


@pytest.mark.parametrize(
    ("source", "source_language", "review_enabled"),
    [
        ("올 어바웃 에단 카터.", Language.KOREAN, False),
        ("“해피 버스데이 투 유.”", Language.KOREAN, False),
        ("He is fine.", Language.ENGLISH, True),
        ("彼は元気です。", Language.JAPANESE, True),
    ],
)
def test_runner_latin_review_exception_does_not_affect_other_paths(
    source: str, source_language: Language, review_enabled: bool,
) -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"All About Ethan Carter."}\n')),
            TransportResult(200, _ok_body('{"0":"All About Ethan Carter."}\n')),
            TransportResult(200, _ok_body('{"0":"All About Ethan Carter."}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=source_language,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
        preserve_korean_latin_review_candidates=review_enabled,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=(source,)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == source
    assert PRESERVED_CANDIDATE_SEGMENTS_KEY not in payload


def test_runner_stages_wrong_target_latin_prose_for_review_only() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"He really is fine."}\n')),
            TransportResult(200, _ok_body('{"0":"He really is fine."}\n')),
            TransportResult(200, _ok_body('{"0":"He really is fine."}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
        preserve_korean_latin_review_candidates=True,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("그는 정말 괜찮다.",)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "He really is fine."
    assert payload[PRESERVED_CANDIDATE_SEGMENTS_KEY] == ["0:0"]
    assert "function_word_residue" in payload["low_confidence"][0]["tags"]


def test_runner_retries_solo_low_confidence_transport_error() -> None:
    transport = FlakyTransport(
        outcomes=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"세계"}\n')),
            RuntimeError("ConnectError"),
            TransportResult(200, _ok_body('{"0":"世界"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好", "0:1": "世界"}
    assert payload["low_confidence"] == []
    assert len(transport.requests) == 3


def test_runner_keeps_chunk_when_solo_low_confidence_transport_exhausts() -> None:
    transport = FlakyTransport(
        outcomes=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"세계"}\n')),
            RuntimeError("ConnectError"),
            RuntimeError("ConnectError"),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "你好"
    assert payload["translations"]["0:1"] == "친구입니다"
    flagged = payload["low_confidence"][0]
    assert flagged["segment_id"] == "0:1"
    assert "low_confidence_retry_transient_failed" in flagged["reasons"]
    assert "fell_back_to_source_after_max_retries" in flagged["reasons"]
    assert "source_residue" in flagged.get("tags", [])
    assert len(transport.requests) == 3


def test_runner_caps_solo_low_confidence_transport_retry_budget() -> None:
    transport = FlakyTransport(
        outcomes=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"세계"}\n')),
            RuntimeError("ConnectError"),
            RuntimeError("ConnectError"),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "你好"
    assert payload["translations"]["0:1"] == "친구입니다"
    assert len(transport.requests) == 3


@pytest.mark.parametrize(
    ("configured_retries", "expected_rescue_calls"),
    [(3, 12), (4, 16)],
)
def test_runner_caps_total_low_confidence_rescue_calls_per_chunk(
    configured_retries: int, expected_rescue_calls: int
) -> None:
    sources = tuple(f"안녕하세요 친구입니다 정말 긴 문장 {idx}" for idx in range(20))
    initial = "\n".join(f'{{"{idx}":"嗯{idx}"}}' for idx in range(20))
    rescue_responses = [
        TransportResult(200, _ok_body(f'{{"0":"嗯{idx}"}}\n'))
        for idx in range(expected_rescue_calls)
    ]
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(initial + "\n")),
            *rescue_responses,
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=configured_retries,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert len(payload["translations"]) == 20
    assert len(payload["low_confidence"]) == 20
    assert len(transport.requests) == expected_rescue_calls + 1
    assert transport.responses == []


def test_runner_rescues_mass_source_residue_with_target_language_micro_batch() -> None:
    sources = tuple(f"안녕하세요 친구입니다 정말 긴 문장 {idx}" for idx in range(8))
    initial = "\n".join(
        (
            f'{{"{idx}":"{text}"}}'
            if idx < 6
            else f'{{"{idx}":"こんにちは、友人です。本当に長い文章です {idx}"}}'
        )
        for idx, text in enumerate(sources)
    )
    rescue = "\n".join(
        f'{{"{idx}":"こんにちは、友人です。本当に長い文章です {idx}"}}'
        for idx in range(6)
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(initial + "\n")),
            TransportResult(200, _ok_body(rescue + "\n")),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.JAPANESE,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["low_confidence"] == []
    assert len(payload["translations"]) == 8
    assert payload["translations"]["0:0"].startswith("こんにちは")
    assert len(transport.requests) == 2

    rescue_payload = transport.requests[1]["payload"]
    messages = rescue_payload["messages"]
    rescue_prompt = messages[-1]["content"]
    assert language_prompt_label(Language.JAPANESE) in rescue_prompt
    assert "中文" not in rescue_prompt
    assert sources[0] in rescue_prompt
    assert sources[5] in rescue_prompt
    assert sources[6] not in rescue_prompt
    assert sources[7] not in rescue_prompt


def test_runner_keeps_mass_source_residue_after_micro_batch_still_echoes() -> None:
    sources = tuple(f"안녕하세요 친구입니다 정말 긴 문장 {idx}" for idx in range(8))
    initial = "\n".join(
        f'{{"{idx}":"{text}"}}' for idx, text in enumerate(sources)
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(initial + "\n")),
            TransportResult(200, _ok_body(initial + "\n")),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=0,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        f"0:{idx}": text for idx, text in enumerate(sources)
    }
    assert len(payload["low_confidence"]) == 8
    assert all(
        "mass_source_residue_after_retry" in item["reasons"]
        for item in payload["low_confidence"]
    )
    assert all(
        "source_residue" in item.get("tags", [])
        for item in payload["low_confidence"]
    )
    assert len(transport.requests) == 2


def test_runner_marks_terminal_source_residue_at_twenty_percent() -> None:
    sources = tuple(f"안녕하세요 친구입니다 정말 긴 문장 {idx}" for idx in range(32))
    initial = "\n".join(
        json.dumps(
            {str(idx): text if idx < 7 else f"中文译文 {idx}"},
            ensure_ascii=False,
        )
        for idx, text in enumerate(sources)
    )
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body(initial + "\n"))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=0,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    systemic = [
        item
        for item in payload["low_confidence"]
        if "mass_source_residue_after_retry" in item["reasons"]
    ]
    assert len(systemic) == 7
    assert len(transport.requests) == 1


def test_runner_keeps_small_chunk_if_all_solo_retries_still_echo_source() -> None:
    sources = ("안녕하세요 친구입니다 정말 긴 문장 0", "오늘도 같이 갑니다 정말 긴 문장 1")
    initial = "\n".join(
        f'{{"{idx}":"{text}"}}' for idx, text in enumerate(sources)
    )
    solo_responses = [
        TransportResult(200, _ok_body(f'{{"0":"{text}"}}\n'))
        for text in sources
    ]
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(initial + "\n")),
            *solo_responses,
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        f"0:{idx}": text for idx, text in enumerate(sources)
    }
    assert len(payload["low_confidence"]) == 2
    assert all(
        "mass_source_residue_after_retry" in item["reasons"]
        for item in payload["low_confidence"]
    )
    assert all(
        "source_residue" in item.get("tags", [])
        for item in payload["low_confidence"]
    )
    assert len(transport.requests) == 3


def test_runner_does_not_fully_refresh_low_confidence_rescue_budget_on_subtask_retry() -> None:
    sources = tuple(f"안녕하세요 친구입니다 정말 긴 문장 {idx}" for idx in range(20))
    initial = "\n".join(f'{{"{idx}":"嗯{idx}"}}' for idx in range(20))
    rescue_responses = [
        TransportResult(200, _ok_body(f'{{"0":"嗯{idx}"}}\n'))
        for idx in range(12)
    ]
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(initial + "\n")),
            *rescue_responses,
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=sources, attempt_count=2))
    )

    payload = json.loads(result.response_content)
    assert len(payload["translations"]) == 20
    assert len(payload["low_confidence"]) == 20
    assert len(transport.requests) == 2
    assert len(transport.responses) == 11


def test_runner_keeps_mostly_translated_text_when_minor_residue_persists() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"元英把脸埋进膝盖里。휴지呜咽着。"}\n')),
            TransportResult(200, _ok_body('{"0":"元英把脸埋进膝盖里。휴지呜咽着。"}\n')),
            TransportResult(200, _ok_body('{"0":"元英把脸埋进膝盖里。휴지呜咽着。"}\n')),
            TransportResult(200, _ok_body('{"0":"元英把脸埋进膝盖里。휴지呜咽着。"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("휴지가 원영 앞을 서성였다.",)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "元英把脸埋进膝盖里。휴지呜咽着。"
    flagged = payload["low_confidence"][0]
    assert flagged["segment_id"] == "0:0"
    assert "force_accepted_after_max_retries" in flagged["reasons"]
    assert "source_residue" in flagged.get("tags", [])
    assert payload[PRESERVED_CANDIDATE_SEGMENTS_KEY] == ["0:0"]


def test_runner_preserves_mixed_candidate_for_configured_language_pair() -> None:
    mixed_candidate = "Переведенный текст с японским именем かな"
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(json.dumps({"0": mixed_candidate}, ensure_ascii=False)),
            )
            for _ in range(4)
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.JAPANESE,
        target_language=Language.RUSSIAN,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("これは翻訳対象の長い日本語文です。",)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == mixed_candidate
    assert payload[PRESERVED_CANDIDATE_SEGMENTS_KEY] == ["0:0"]
    assert "source_residue" in payload["low_confidence"][0].get("tags", [])


def test_runner_keeps_mixed_candidate_after_systemic_residue_retry_exhausts() -> None:
    sources = tuple(f"긴 한국어 원문 문장 {idx}" for idx in range(8))
    mixed = tuple(f"已经翻译的正文 {idx} 닉네임" for idx in range(8))
    initial = "\n".join(
        json.dumps({str(idx): text}, ensure_ascii=False)
        for idx, text in enumerate(mixed)
    )
    source_retry = "\n".join(
        json.dumps({str(idx): text}, ensure_ascii=False)
        for idx, text in enumerate(sources)
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(initial + "\n")),
            TransportResult(200, _ok_body(source_retry + "\n")),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=0,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        f"0:{idx}": text for idx, text in enumerate(mixed)
    }
    assert set(payload[PRESERVED_CANDIDATE_SEGMENTS_KEY]) == {
        f"0:{idx}" for idx in range(8)
    }
    assert all(
        "source_residue" in entry.get("tags", [])
        for entry in payload["low_confidence"]
    )


def test_runner_force_accepts_target_language_low_conf_when_no_residue() -> None:
    """When the model's last attempt has NO source-language residue
    (it's a flawed target-language guess, not Korean leak), the runner
    saves that guess instead of source-passthrough — a questionable
    translated line is easier to fix than a fully-untranslated source
    line."""

    transport = FakeTransport(
        responses=[
            # Initial: idx 0 fine, idx 1 fails length-ratio (very short)
            TransportResult(200, _ok_body('{"0":"你好朋友啊"}\n{"1":"嗯"}\n')),
            # Three retries all return the same short Chinese.
            TransportResult(200, _ok_body('{"1":"嗯"}\n')),
            TransportResult(200, _ok_body('{"1":"嗯"}\n')),
            TransportResult(200, _ok_body('{"1":"嗯"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        min_length_ratio=0.5,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요친구야", "긴 문장입니다 정말로요"))),
    )

    payload = json.loads(result.response_content)
    # idx 1's last attempt "嗯" is non-residue target-language output —
    # keep it instead of source-passthrough so the user has something translated.
    assert payload["translations"]["0:1"] == "嗯"
    flagged = next(
        x for x in payload["low_confidence"] if x["segment_id"] == "0:1"
    )
    assert "force_accepted_after_max_retries" in flagged["reasons"]
    assert "source_residue" not in flagged.get("tags", [])


def test_runner_does_not_promote_foreign_language_leak_to_source_residue() -> None:
    candidate = "士兵们 and 乌修勒看着再次走向森林的艾布尔。"
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(json.dumps({"0": candidate}, ensure_ascii=False)),
            ),
            TransportResult(
                200,
                _ok_body(json.dumps({"0": candidate}, ensure_ascii=False)),
            ),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("군인들은 숲으로 향하는 에이블을 보았다.",)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == candidate
    flagged = payload["low_confidence"][0]
    assert "function_word_residue" in flagged.get("tags", [])
    assert "source_residue" not in flagged.get("tags", [])


def test_runner_prefers_target_language_retry_over_source_echo_when_low_confidence() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    '{"0":"청년의 성기에 살모사의 머리처럼 긴 문장이 이어졌다"}\n'
                ),
            ),
            TransportResult(200, _ok_body('{"0":"目录"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        min_length_ratio=0.8,
        low_confidence_max_retries=1,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask(
                sources=("청년의 성기에 살모사의 머리처럼 긴 문장이 이어졌다",)
            )
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "目录"
    flagged = payload["low_confidence"][0]
    assert flagged["segment_id"] == "0:0"
    assert "force_accepted_after_max_retries" in flagged["reasons"]
    assert "fell_back_to_source_after_max_retries" not in flagged["reasons"]
    assert "source_residue" not in flagged.get("tags", [])


def test_runner_with_max_retries_zero_falls_back_to_single_call() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"1":"세계"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=0,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=("안녕하세요", "친구입니다")))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好", "0:1": "세계"}
    flagged_ids = {item["segment_id"] for item in payload["low_confidence"]}
    assert flagged_ids == {"0:1"}
    assert len(transport.responses) == 0
# Pathological model responses — end-to-end through the runner.
#
# Each scenario simulates a real failure mode observed in production
# (custom literary preset, deepseek-v4-pro). The runner must recover
# without losing data — Layer 1 (decoder fallback), Layer 2 (system
# prompt augmentation), Layer 3 (retry banner), Layer 4 (positional
# rescue) are all under test here.


_LITERARY_PERSONA_PROMPT = (
    "Role: 你是一名顶尖的、深谙中韩双语文化的出版级小说翻译家。\n"
    "你的最高准则是：在绝对不改变原文语义与意境的前提下，用精准、"
    "流畅、有质感的现代中文进行还原。\n"
    "翻译完成后必须严格遵守以下原则：保持原文行数、保留专有名词、"
    "拒绝古风词汇、不擅自添加情绪。"
)


def _literary_preset() -> PromptPreset:
    return PromptPreset(
        id="literary-custom",
        name="literary",
        kind=PromptKind.TRANSLATION,
        system_prompt=_LITERARY_PERSONA_PROMPT,
        suffix_prompt="",
        thinking_prompt="",
    )


def _eight_line_subtask() -> Subtask:
    return _make_subtask(
        sources=tuple(f"原文 {i}" for i in range(8)),
    )


def _fast_retry_model() -> ModelConfig:
    """Same shape as ``_model()``; kept as a named helper for the
    pathological-response tests. Retry backoff is zeroed globally by the
    ``_zero_retry_backoff`` autouse fixture, and the transport-retry
    budget is set on the runner via ``transport_retry_attempts``."""

    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("key",),
        thinking_level=ThinkingLevel.OFF,
    )


def test_runner_recovers_pretty_printed_object_response() -> None:
    """Model emits one big multi-line JSON object instead of one
    independent JSONL row per index. Layer 1 fallback must rescue
    this without ever raising line_count_mismatch."""

    pretty = (
        "{\n"
        + ",\n".join(f'  "{i}": "译文 {i}"' for i in range(8))
        + "\n}\n"
    )
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body(pretty))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_eight_line_subtask()))

    payload = json.loads(result.response_content)
    assert len(payload["translations"]) == 8
    # No retry — fallback succeeded on the first call.
    assert len(transport.requests) == 1


def test_runner_recovers_response_wrapped_in_translations_key() -> None:
    wrapped = (
        '{"translations": {'
        + ", ".join(f'"{i}": "out{i}"' for i in range(8))
        + "}}"
    )
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body(wrapped))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_eight_line_subtask()))

    payload = json.loads(result.response_content)
    assert len(payload["translations"]) == 8


def test_runner_recovers_json_array_positionally() -> None:
    array = "[" + ", ".join(f'"out{i}"' for i in range(8)) + "]"
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body(array))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_eight_line_subtask()))

    payload = json.loads(result.response_content)
    assert len(payload["translations"]) == 8


def test_runner_pure_prose_response_with_count_match_fails_with_fallback_result() -> None:
    """Plain prose has no stable segment keys, even when its line count
    happens to match. It must fall back to proofreading instead of being
    silently aligned by position."""

    prose_response = "\n".join(f"译文行 {i}" for i in range(8)) + "\n"
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(prose_response)),
            TransportResult(200, _ok_body(prose_response)),
            TransportResult(200, _ok_body(prose_response)),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(runner.run(_eight_line_subtask()))

    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {
        f"0:{i}": f"原文 {i}" for i in range(8)
    }
    flagged = {item["segment_id"] for item in payload["low_confidence"]}
    assert flagged == {f"0:{i}" for i in range(8)}
    for item in payload["low_confidence"]:
        assert "line_count_mismatch_after_max_retries" in item["reasons"]
        assert item["tags"] == ["source_residue"]
    assert len(transport.requests) == 3


def test_runner_pure_prose_response_with_count_mismatch_fails_with_fallback_result() -> None:
    """Plain prose with the wrong line count preserves source fallback
    for review, but the chunk itself must be marked failed."""

    short_prose = "\n".join(f"译文行 {i}" for i in range(5)) + "\n"
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(short_prose)),
            TransportResult(200, _ok_body(short_prose)),
            TransportResult(200, _ok_body(short_prose)),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(runner.run(_eight_line_subtask()))

    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {
        f"0:{i}": f"原文 {i}" for i in range(8)
    }
    flagged = {item["segment_id"] for item in payload["low_confidence"]}
    assert flagged == {f"0:{i}" for i in range(8)}
    for item in payload["low_confidence"]:
        assert "line_count_mismatch_after_max_retries" in item["reasons"]
        assert item["tags"] == ["source_residue"]


def test_runner_format_retry_banner_prepended_on_second_attempt() -> None:
    """First attempt fails decode → second attempt's user message must
    carry the FORMAT RETRY banner so the next sampling pass has a
    much better chance of complying."""

    bad = "Hello there!\nNo JSON here.\n"
    good = "\n".join(f'{{"{i}":"out {i}"}}' for i in range(8)) + "\n"
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(bad)),
            TransportResult(200, _ok_body(good)),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_eight_line_subtask()))

    first_user = transport.requests[0]["payload"]["messages"][-1]["content"]
    second_user = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "FORMAT RETRY" not in first_user
    assert "FORMAT RETRY" in second_user


def test_runner_does_not_inject_format_hint_when_preset_already_mentions_jsonl() -> None:
    """A custom preset that already explains the JSONL format must NOT
    receive a duplicate runtime hint — avoid noise + drift."""

    aware = PromptPreset(
        id="aware-custom",
        name="aware",
        kind=PromptKind.TRANSLATION,
        system_prompt=(
            "Role: editorial translator.\n"
            "Output as JSONLINE: one JSON object per source index."
        ),
        suffix_prompt="",
        thinking_prompt="",
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"x"}\n{"1":"y"}\n'))
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=aware,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    asyncio.run(runner.run(_make_subtask()))

    system_message = transport.requests[0]["payload"]["messages"][0]["content"]
    assert "[Output transport — runtime protocol]" not in system_message


def test_runner_does_not_inject_format_hint_into_system_preset() -> None:
    """Built-in system presets carry the JSONLINE suffix already; the
    runtime hint is only meant to backstop custom presets."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"x"}\n{"1":"y"}\n'))
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    system_message = transport.requests[0]["payload"]["messages"][0]["content"]
    assert "[Output transport — runtime protocol]" not in system_message


def test_runner_broken_json_lines_fail_with_fallback_result() -> None:
    """If lines look like JSON fragments (start with ``{`` or ``[``)
    but don't decode, silently aligning them to source positions would
    corrupt output."""

    half_json = "\n".join(f'{{"x{i}": broken' for i in range(8)) + "\n"
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(half_json)),
            TransportResult(200, _ok_body(half_json)),
            TransportResult(200, _ok_body(half_json)),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(runner.run(_eight_line_subtask()))

    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {
        f"0:{i}": f"原文 {i}" for i in range(8)
    }
    assert {
        item["segment_id"] for item in payload["low_confidence"]
    } == {f"0:{i}" for i in range(8)}


def test_runner_thinking_wrapped_plain_prose_fails_with_fallback_result() -> None:
    """Thinking-wrapped plain prose is still unkeyed prose, so it must
    fall back instead of being accepted by count."""

    response = (
        "<think>\nplanning the eight-line response\n</think>\n"
        + "\n".join(f"译文 {i}" for i in range(8))
        + "\n"
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(response)),
            TransportResult(200, _ok_body(response)),
            TransportResult(200, _ok_body(response)),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(SubtaskFailedWithResult) as exc_info:
        asyncio.run(runner.run(_eight_line_subtask()))

    payload = json.loads(exc_info.value.result.response_content)
    assert payload["translations"] == {
        f"0:{i}": f"原文 {i}" for i in range(8)
    }
    assert {
        item["segment_id"] for item in payload["low_confidence"]
    } == {f"0:{i}" for i in range(8)}


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


def test_runner_allows_short_affirmative_collision_across_distinct_sources() -> None:
    """Two Korean affirmatives ("응" / "어") legitimately translate to the
    same short Chinese "嗯。". Old duplicate-drift detection raised on
    this; it must now pass through."""

    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body('{"0":"嗯。"}\n{"1":"嗯。"}\n'),
            )
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=("응.", "어."))))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "嗯。", "0:1": "嗯。"}


def _no_retry_model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("k",),
    )


def test_runner_detects_long_translation_duplicate_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two distinct sources producing the same long translation IS
    model-laziness drift. With no partial-accept re-ask the runner gets
    one shot, detects drift, and pushes both lines to proofreading
    instead of trusting the duplicate output."""

    monkeypatch.setattr(
        "transoria.workflows.translation.runner._PARTIAL_ACCEPT_MAX_RETRIES", 0
    )
    long_dst = "这是一个相当长的句子，模型不应该重复使用。"
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(f'{{"0":"{long_dst}"}}\n{{"1":"{long_dst}"}}\n'),
            )
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_no_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask(
                sources=("완전히 다른 한국어 문장 1.", "완전히 다른 한국어 문장 2.")
            )
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "완전히 다른 한국어 문장 1.",
        "0:1": "완전히 다른 한국어 문장 2.",
    }
    assert {
        item["segment_id"] for item in payload["low_confidence"]
    } == {"0:0", "0:1"}
    for item in payload["low_confidence"]:
        assert "duplicate_drift_after_max_retries" in item["reasons"]
        assert item["tags"] == ["source_residue"]


def test_runner_allows_duplicate_translation_for_equivalent_sources() -> None:
    source_a = "―너 황재용 그 새끼랑 뭔 일 있었냐?"
    source_b = "― 너 황재용 그 새끼랑 뭔 일 있었냐?"
    translation = "— 你跟黄在容那小子之间出过什么事吗？"
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    f'{{"0":"{translation}"}}\n'
                    f'{{"1":"{translation}"}}\n'
                ),
            )
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_no_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=(source_a, source_b)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": translation,
        "0:1": translation,
    }


def test_runner_broadcasts_single_response_for_equivalent_sources() -> None:
    source_a = "―너 황재용 그 새끼랑 뭔 일 있었냐?"
    source_b = "― 너 황재용 그 새끼랑 뭔 일 있었냐?"
    translation = "— 你跟黄在容那小子之间出过什么事吗？"
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(f'{{"0":"{translation}"}}\n'))
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_no_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(_make_subtask(sources=(source_a, source_b)))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": translation,
        "0:1": translation,
    }


def test_runner_partial_retries_duplicate_drift_then_succeeds() -> None:
    """Drift on first call → runner re-pends the suspicious indices and
    asks the model again with a narrow sub-chunk. Distinct retry
    translations clear the drift; chunk completes without orchestrator
    split."""

    long_dst = "这是一个相当长的句子，模型不应该重复使用。"
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(f'{{"0":"{long_dst}"}}\n{{"1":"{long_dst}"}}\n'),
            ),
            TransportResult(
                200,
                _ok_body('{"0":"译文甲完全不同。"}\n{"1":"译文乙也不一样。"}\n'),
            ),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask(
                sources=("완전히 다른 한국어 문장 1.", "완전히 다른 한국어 문장 2.")
            )
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "译文甲完全不同。",
        "0:1": "译文乙也不一样。",
    }
    assert len(transport.requests) == 2


def test_runner_retries_near_duplicate_drift_with_context() -> None:
    context = ("previous paragraph.",)
    sources = (
        "Martin Heidegger was right.",
        "Everyone is born as many and dies as one.",
        "He heard a sound beside him.",
    )
    first = (
        '{"0":"清晨伴随着闪光降临。拂晓时分，透过窗户隐约瞥见了黎明，但要说这是完整的早晨，亮度又太低，更何况胃里翻涌的异物感和床单上黏着的气味让我头晕目眩。"}\n'
        '{"1":"清晨伴随着一道闪光降临。拂晓时分，透过窗户隐约瞥见了晨光，但那亮度远算不上完整的早晨，更别提胃里翻涌的异物感和床单上黏附的气味，让脑袋昏沉沉的。"}\n'
        '{"2":"他听见身旁传来动静。"}\n'
    )
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(first)),
            TransportResult(
                200,
                _ok_body(
                    '{"0":"海德格尔是对的。"}\n'
                    '{"1":"每个人都以众人的形式出生，以一个人的形式死去。"}\n'
                ),
            ),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(_make_subtask_with_context(context=context, sources=sources))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "海德格尔是对的。",
        "0:1": "每个人都以众人的形式出生，以一个人的形式死去。",
        "0:2": "他听见身旁传来动静。",
    }
    retry_prompt = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "[Context" in retry_prompt
    assert "previous paragraph." in retry_prompt
    assert "He heard a sound beside him." not in retry_prompt


def test_runner_retries_adjacent_medium_near_duplicate_drift() -> None:
    sources = (
        "이정의 순종적인 태도에 남자는 기꺼운 듯했다.",
        "이정은 남자와 시선을 감히 마주하지 못하고 고개를 숙였다.",
    )
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    '{"0":"李正不敢与男人对视，低下了头。"}\n'
                    '{"1":"李正不敢与那男人对视，低下了头。"}\n'
                ),
            ),
            TransportResult(
                200,
                _ok_body(
                    '{"0":"男人似乎很满意李正顺从的态度。"}\n'
                    '{"1":"李正不敢与那个男人对视，垂下了头。"}\n'
                ),
            ),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "男人似乎很满意李正顺从的态度。",
        "0:1": "李正不敢与那个男人对视，垂下了头。",
    }
    assert len(transport.requests) == 2


def test_runner_marks_batch_line_when_solo_retry_reveals_shifted_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No batch-level partial-accept re-ask so the crafted responses are
    # consumed by the batch call + solo retries in the intended order.
    monkeypatch.setattr(
        "transoria.workflows.translation.runner._PARTIAL_ACCEPT_MAX_RETRIES", 0
    )
    sources = (
        "이정의 순종적인 태도에 남자는 기꺼운 심정을 숨기지 않았다. 웃는 얼굴로 이정의 몸에 댔던 손을 뗐다.",
        "이정은 남자와 시선을 감히 마주하지 못하고 고개를 숙였다. 남자는 시종일관 부드러운 표정을 유지하고 있었다.",
        "이 남자는, 위험한 사람이다.",
    )
    shifted_translation = (
        "李正不敢与男人对视，低下了头。男人自始至终都保持着温和的表情，"
        "但奇怪的是，与他目光相接却异常困难。"
    )
    fixed_translation = (
        "李正不敢与那男人对视，低下了头。男人自始至终都维持着温和的表情，"
        "不知为何，与他目光相接却异常困难。"
    )
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    f'{{"0":"{shifted_translation}"}}\n'
                    '{"1":"这个男人，很危险。"}\n'
                ),
            ),
            TransportResult(200, _ok_body(f'{{"0":"{fixed_translation}"}}\n')),
            TransportResult(200, _ok_body('{"0":"这个男人，很危险。"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_no_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": shifted_translation,
        "0:1": fixed_translation,
        "0:2": "这个男人，很危险。",
    }
    flagged = [
        item for item in payload["low_confidence"] if item["segment_id"] == "0:0"
    ]
    assert flagged
    assert (
        "duplicate_drift_after_low_confidence_retry" in flagged[0]["reasons"]
    )
    assert flagged[0]["tags"] == ["possible_duplicate"]


def test_runner_does_not_retry_non_adjacent_near_duplicate_drift() -> None:
    sources = (
        "첫 번째 문장은 완전히 다른 내용이다.",
        "중간 문장은 또 다른 장면이다.",
        "세 번째 문장도 첫 문장과 다른 내용이다.",
    )
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    '{"0":"李正不敢与男人对视，低下了头。"}\n'
                    '{"1":"中间这一句是完全不同的内容。"}\n'
                    '{"2":"李正不敢与那男人对视，低下了头。"}\n'
                ),
            ),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask(sources=sources)))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "李正不敢与男人对视，低下了头。",
        "0:1": "中间这一句是完全不同的内容。",
        "0:2": "李正不敢与那男人对视，低下了头。",
    }
    assert len(transport.requests) == 1


def test_runner_accepts_short_domain_vocab_collision() -> None:
    """Two distinct sources producing the same short translation is a
    legitimate domain-vocabulary collision (chess: 체크 / 체크메이트 →
    将军), not model-laziness drift. The runner must accept it."""

    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    '{"0":"“将军。”"}\n{"1":"“将军。”"}\n{"2":"“将军。”"}\n'
                ),
            )
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_no_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask(
                sources=("“체크메이트.”", "“체크.”", "“체크.”")
            )
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "“将军。”",
        "0:1": "“将军。”",
        "0:2": "“将军。”",
    }
    assert len(transport.requests) == 1


def test_runner_detects_three_way_short_duplicate_drift() -> None:
    """Three+ distinct sources sharing one short translation IS drift
    even at short length. If it cannot be retried, the runner falls
    back to source lines for proofreading."""

    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body('{"0":"好。"}\n{"1":"好。"}\n{"2":"好。"}\n'),
            )
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_no_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    sources = (
        "정말 길고 다른 문장 하나입니다.",
        "이것도 다른 문장이에요 두 번째.",
        "세 번째도 완전히 다른 내용.",
    )
    result = asyncio.run(
        runner.run(
            _make_subtask(sources=sources)
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        f"0:{idx}": source for idx, source in enumerate(sources)
    }
    assert {
        item["segment_id"] for item in payload["low_confidence"]
    } == {"0:0", "0:1", "0:2"}


def test_runner_line_count_fallback_can_still_recover_with_solo_retry() -> None:
    short_prose = "\n".join(f"译文行 {i}" for i in range(1)) + "\n"
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body(short_prose)),
            TransportResult(200, _ok_body('{"0":"你好"}\n')),
            TransportResult(200, _ok_body('{"0":"世界"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_no_retry_model(),
        prompt_preset=_literary_preset(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        low_confidence_max_retries=1,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask(sources=("안녕", "세계"))
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "你好",
        "0:1": "世界",
    }
    assert payload["low_confidence"] == []


def test_runner_debug_log_filename_uses_subtask_id(tmp_path) -> None:
    """Parent chunk and its split children share the first segment_id, so
    the old chunk-derived filename collided. Use subtask.id to keep them
    distinct under the cache's debug/ folder."""

    from pathlib import Path

    debug_dir = tmp_path / "debug"
    transport = FakeTransport(
        responses=[TransportResult(200, _ok_body('{"0":"x"}\n{"1":"y"}\n'))]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        debug_log_dir=debug_dir,
    )

    sub = _make_subtask()
    # Replace the auto-generated subtask_id with a split-child shape so we
    # exercise the path that used to collide.
    from dataclasses import replace as dc_replace

    sub = dc_replace(sub, id="chunk-00213.s1.0")
    asyncio.run(runner.run(sub))

    expected = debug_dir / "chunk-00213.s1.0.json"
    assert expected.exists(), list(debug_dir.iterdir())


def test_runner_low_conf_retry_uses_contextual_single_item_calls() -> None:

    transport = FakeTransport(
        responses=[
            # Initial: idx 0 fine, idx 1 + 2 fail (residue)
            TransportResult(
                200,
                _ok_body('{"0":"你好"}\n{"1":"네."}\n{"2":"응."}\n'),
            ),
            # Solo retries echo back with key "0" (since solo asks for
            # chunk_index=0). Positional decode ignores the key anyway.
            TransportResult(200, _ok_body('{"0":"是的。"}\n')),
            TransportResult(200, _ok_body('{"0":"嗯。"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=3,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask_with_context(
                context=("앞선 문맥",),
                sources=("안녕하세요친구야", "네 알겠습니다", "응 그래요"),
            )
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:0"] == "你好"
    assert payload["translations"]["0:1"] == "是的。"
    assert payload["translations"]["0:2"] == "嗯。"
    assert len(transport.requests) == 3
    solo_prompts = [
        req["payload"]["messages"][-1]["content"]
        for req in transport.requests[1:]
    ]
    for p in solo_prompts:
        assert "앞선 문맥" in p
        assert '"0": "' in p
        assert '"1": "' not in p
        assert '"2": "' not in p


def test_runner_solo_retry_filters_glossary_to_current_source() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="Alpha", dst="阿尔法"),
            GlossaryEntry(src="Beta", dst="贝塔"),
        )
    )
    transport = FakeTransport(
        responses=[
            TransportResult(
                200, _ok_body('{"0":"你好，阿尔法"}\n{"1":"네 Beta"}\n')
            ),
            TransportResult(200, _ok_body('{"0":"好的，贝塔。"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        enable_confidence_check=True,
        low_confidence_max_retries=1,
    )

    result = asyncio.run(
        runner.run(
            _make_subtask(
                sources=("안녕하세요 Alpha", "네 알겠습니다 Beta"),
                glossary=glossary,
            )
        )
    )

    payload = json.loads(result.response_content)
    assert payload["translations"]["0:1"] == "好的，贝塔。"
    solo_user_message = transport.requests[1]["payload"]["messages"][-1]["content"]
    assert "[Glossary]" in solo_user_message
    assert "Beta -> 贝塔" in solo_user_message
    assert "Alpha -> 阿尔法" not in solo_user_message


def test_runner_decodes_positionally_when_response_count_matches() -> None:
    """Model returns translations in correct order but with wrong JSON
    keys (e.g. echoes back stale labels). When response line count
    equals expected segment count, positional zip recovers — content
    lands on the right segment regardless of mislabeled keys."""

    # Keys "5" and "9" don't match expected indices 0 and 1, but the
    # response order matches source order so positional decode rescues.
    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"5":"你好"}\n{"9":"世界"}\n'))
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
    assert payload["translations"] == {"0:0": "你好", "0:1": "世界"}


def test_runner_prefers_complete_keys_when_response_order_differs() -> None:
    """If JSONL keys are complete, row order is weaker evidence than keys.
    Trusting position here would swap translations between source lines."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"1":"世界"}\n{"0":"你好"}\n'))
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
    assert payload["translations"] == {"0:0": "你好", "0:1": "世界"}


def test_runner_retries_mixed_keys_instead_of_positionally_guessing() -> None:
    """A response with one expected key and one stray key is ambiguous:
    part of it may be aligned by key, but positional rescue could corrupt
    the missing segment. Retry instead of guessing."""

    transport = FakeTransport(
        responses=[
            TransportResult(200, _ok_body('{"0":"你好"}\n{"9":"错位候选"}\n')),
            TransportResult(200, _ok_body('{"1":"世界"}\n')),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert payload["translations"] == {"0:0": "你好", "0:1": "世界"}
    assert len(transport.requests) == 2


def test_runner_omits_context_from_batch_prompt() -> None:
    """Context stored in the subtask cache must not be sent to the model."""

    context = ("previous sentence one.", "previous sentence two.")
    sources = (
        "line alpha",
        "line beta",
        "line gamma",
        "line delta",
    )
    transport = FakeTransport(
        responses=[
            TransportResult(
                200,
                _ok_body(
                    '{"0":"甲。"}\n'
                    '{"1":"乙。"}\n'
                    '{"2":"丙。"}\n'
                    '{"3":"丁。"}\n'
                ),
            ),
        ]
    )
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(
        runner.run(_make_subtask_with_context(context=context, sources=sources))
    )

    payload = json.loads(result.response_content)
    assert payload["translations"] == {
        "0:0": "甲。",
        "0:1": "乙。",
        "0:2": "丙。",
        "0:3": "丁。",
    }
    first_prompt = transport.requests[0]["payload"]["messages"][-1]["content"]
    assert "[Context" not in first_prompt
    assert "previous sentence one." not in first_prompt
    assert len(transport.requests) == 1


def test_runner_rejects_extra_context_line_instead_of_shifted_keys() -> None:
    context = ("previous sentence",)
    sources = ("line alpha", "line beta")
    runner = TranslationSubtaskRunner(
        client=LlmClient(transport=FakeTransport()),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )
    chunk, metadata = _decode_subtask_payload(
        _make_subtask_with_context(context=context, sources=sources).request_payload
    )

    translations, missing = runner._decode_partial(
        '{"0":"上文。"}\n{"1":"甲。"}\n{"2":"乙。"}\n',
        metadata,
        chunk.context_lines,
    )
    assert translations == {}
    assert missing == frozenset({0, 1})
