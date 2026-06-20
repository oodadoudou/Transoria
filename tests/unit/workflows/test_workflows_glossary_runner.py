from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

import pytest

from transoria.domain import Language
from transoria.llm import LlmClient, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask
from transoria.workflows.glossary import (
    GlossaryChunk,
    GlossarySubtaskRunner,
    encode_glossary_payload,
)
from transoria.workflows.glossary.runner import decode_glossary_subtask_response
from transoria.workflows.fake_name import FakeNameSession


@dataclass
class FakeTransport:
    responses: list[TransportResult | BaseException] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@dataclass
class DelayedTransport:
    responses: list[TransportResult | BaseException] = field(default_factory=list)
    delay: float = 0.01
    active: int = 0
    max_active: int = 0

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        finally:
            self.active -= 1


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


def _fast_retry_model() -> ModelConfig:
    return replace(
        _model(),
    )


def _ok_body(content: str) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 70},
    }


def _make_subtask() -> Subtask:
    chunk = GlossaryChunk(
        chunk_id="chunk-00000",
        source_file=Path("/in/Novel.txt"),
        text="신해범 walked into the room with 공이.\n흑룡 watched silently.",
    )
    return Subtask(id=chunk.chunk_id, task_id="t1", request_payload=encode_glossary_payload(chunk))


def test_runner_returns_decoded_entries_in_response_payload() -> None:
    body = _ok_body(
        '{"src":"신해범","dst":"申海范","type":"男性角色"}\n'
        '{"src":"공이","dst":"孔二","type":"性别未知/不适用"}\n'
        '{"src":"흑룡","dst":"黑龙","type":"特殊生物"}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        transport_retry_attempts=0,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    sources = {entry["src"] for entry in payload["entries"]}
    assert sources == {"신해범", "공이", "흑룡"}
    assert all(entry["info"] for entry in payload["entries"])
    assert result.input_tokens == 30
    assert result.output_tokens == 70


def test_runner_records_decode_issues_without_raising() -> None:
    body = _ok_body(
        '{"src":"신해범","dst":"申海范","type":"男性角色"}\n'
        "garbage that cannot be parsed\n"
        '{"src":"","dst":"missing src","type":"x"}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        transport_retry_attempts=0,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert {entry["src"] for entry in payload["entries"]} == {"신해범"}
    assert payload["issues"]


def test_runner_passes_source_text_section_in_user_prompt() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    messages = transport.calls[0]["payload"]["messages"]
    assert [message["role"] for message in messages] == ["user"]
    user_message = messages[0]["content"]
    assert "只输出 JSONLINE" in user_message
    assert "[Source Text]" in user_message
    assert "신해범" in user_message
    assert "omit that candidate entirely" in user_message
    assert user_message.rstrip().endswith("No prose, no Markdown, no code fence.")


def test_runner_injects_first_name_only_in_prompt() -> None:
    body = _ok_body('{"src":"신해범","dst":"申海范","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        name_injections={"/in/Novel.txt": "신해범"},
    )

    result = asyncio.run(runner.run(_make_subtask()))

    user_message = transport.calls[0]["payload"]["messages"][-1]["content"]
    assert "【신해범】신해범 walked" in user_message
    payload = json.loads(result.response_content)
    assert payload["entries"][0]["src"] == "신해범"


def test_runner_masks_fake_names_in_prompt_and_restores_response() -> None:
    body = _ok_body('{"src":"蓝霁云","dst":"申海范","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        fake_name_session=FakeNameSession(),
    )
    chunk = GlossaryChunk(
        chunk_id="chunk-rare",
        source_file=Path("/in/Rare.txt"),
        text="rare 龘 name",
    )

    result = asyncio.run(
        runner.run(
            Subtask(
                id=chunk.chunk_id,
                task_id="t1",
                request_payload=encode_glossary_payload(chunk),
            )
        )
    )

    user_message = transport.calls[0]["payload"]["messages"][-1]["content"]
    assert "龘" not in user_message
    assert "蓝霁云" in user_message
    payload = json.loads(result.response_content)
    assert payload["entries"][0]["src"] == "龘"


def test_runner_injects_system_thinking_when_force_enabled_on_non_thinking_model() -> None:
    """force_thinking_enable opts a non-thinking model into the
    system reasoning prefix without sending a provider thinking
    field (which would 4xx). Verify both: prompt has guidance, and the
    OpenAI payload omits any thinking parameter."""

    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    preset = replace(
        default_preset(PromptKind.GLOSSARY),
        thinking_prompt="<why>\nthink first\n</why>",
    )
    model = replace(_model(thinking=ThinkingLevel.OFF), force_thinking_enable=True)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=preset,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    user_message = transport.calls[0]["payload"]["messages"][0]["content"]
    assert "<why>" not in user_message
    assert "Before answering" in user_message
    # ``thinking_level=OFF`` serializes to an explicit ``disabled`` flag
    # uniformly. Non-reasoning providers ignore the unknown body field.
    assert transport.calls[0]["payload"]["thinking"] == {"type": "disabled"}


def test_runner_enforces_target_language_for_type_field_regardless_of_preset() -> None:
    """The ``type`` language constraint is a runtime-level rule. Even
    when the active preset is fully custom and says nothing about
    language, the runner must append the constraint to every call so
    no user can accidentally drift the schema by writing their own
    system prompt."""

    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    custom_preset = replace(
        default_preset(PromptKind.GLOSSARY),
        system_prompt="extract names",  # custom, says nothing about language
        suffix_prompt="output JSON",
        thinking_prompt="",
    )
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=custom_preset,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    user_message = transport.calls[0]["payload"]["messages"][0]["content"]
    # Target language is Simplified Chinese — runtime layer must inject
    # the language clause referring to that language by name.
    assert "Simplified Chinese (简体中文)" in user_message
    assert (
        "never mix languages" in user_message
        or "never fall back to English" in user_message
    )


def test_runner_skips_thinking_prompt_when_neither_native_nor_forced() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    preset = replace(
        default_preset(PromptKind.GLOSSARY),
        thinking_prompt="<why>\nthink first\n</why>",
    )
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(thinking=ThinkingLevel.OFF),
        prompt_preset=preset,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    user_message = transport.calls[0]["payload"]["messages"][0]["content"]
    assert "<why>" not in user_message


def test_runner_includes_system_thinking_when_model_thinking_enabled() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    preset = replace(
        default_preset(PromptKind.GLOSSARY),
        thinking_prompt="<why>\nreason first\n</why>",
    )
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(thinking=ThinkingLevel.MEDIUM),
        prompt_preset=preset,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    user_message = transport.calls[0]["payload"]["messages"][0]["content"]
    assert "<why>" not in user_message
    assert "Before answering" in user_message
    assert transport.calls[0]["payload"]["thinking"] == {
        "type": "enabled",
    }


def test_decode_glossary_subtask_response_round_trips() -> None:
    payload = json.dumps(
        {
            "entries": [
                {"src": "a", "dst": "A", "info": "I"},
                {"src": "b", "dst": "B", "info": ""},
            ],
            "issues": [{"line": "x", "reason": "y"}],
        },
        ensure_ascii=False,
    )

    entries, issues = decode_glossary_subtask_response(payload)

    assert [(e.src, e.dst, e.info) for e in entries] == [("a", "A", "I"), ("b", "B", "")]
    assert issues == ({"line": "x", "reason": "y"},)


def test_runner_retries_when_response_is_pure_markdown_table_no_header() -> None:
    """When the LLM returns a Markdown table with no recognizable
    header, decoder yields zero entries + many issues. The runner
    should retry; if a later attempt provides JSONL, that succeeds."""

    bad_body = _ok_body(
        "好的，分析如下：\n"
        "| 男性角色 | 미아 | 米亚 | 主角 |\n"
        "| 男性角色 | 로건 | 罗根 | 父亲 |\n"
        "| 命名地理 | 벨로리아 | 贝洛利亚 | 王国 |\n"
        "| 命名地理 | 인간계 | 人间界 | 人类世界 |\n"
        "请确认。\n"
    )
    good_body = _ok_body(
        '{"src":"미아","dst":"米亚","type":"男性角色"}\n'
        '{"src":"로건","dst":"罗根","type":"男性角色"}\n'
    )
    transport = FakeTransport(
        responses=[TransportResult(200, bad_body), TransportResult(200, good_body)]
    )
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    assert len(transport.calls) == 2  # one retry triggered
    first_prompt = transport.calls[0]["payload"]["messages"][0]["content"]
    second_prompt = transport.calls[1]["payload"]["messages"][0]["content"]
    assert "FORMAT RETRY" not in first_prompt
    assert "FORMAT RETRY" in second_prompt
    payload = json.loads(result.response_content)
    assert {e["src"] for e in payload["entries"]} == {"미아", "로건"}


def test_runner_caps_transport_retries_for_best_effort_extraction() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(500, {"error": "temporary"}),
            TransportResult(500, {"error": "still temporary"}),
            TransportResult(500, {"error": "temporary again"}),
            TransportResult(500, {"error": "temporary again"}),
            TransportResult(500, {"error": "temporary again"}),
        ]
    )
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        transport_retry_attempts=20,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(Exception) as caught:
        asyncio.run(runner.run(_make_subtask()))

    assert "HTTP 500" in str(caught.value)
    assert len(transport.calls) == 4


def test_runner_retries_transport_error_for_high_concurrency_glossary_requests() -> None:
    transport = FakeTransport(
        responses=[
            TransportResult(500, {"error": "temporary"}),
            TransportResult(200, _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')),
        ]
    )
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=replace(_fast_retry_model(), concurrency_limit=60),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert payload["entries"] == [{"src": "x", "dst": "y", "info": "男性角色"}]
    assert len(transport.calls) == 2


def test_runner_splits_high_concurrency_glossary_timeout() -> None:
    body_1 = _ok_body('{"src":"신해범","dst":"申海范","type":"男性角色"}\n')
    body_2 = _ok_body('{"src":"공이","dst":"孔二","type":"性别未知/不适用"}\n')
    transport = FakeTransport(
        responses=[
            TimeoutError("ReadTimeout"),
            TransportResult(200, body_1),
            TransportResult(200, body_2),
        ]
    )
    model = replace(_fast_retry_model(), concurrency_limit=60, timeout_seconds=600.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert {entry["src"] for entry in payload["entries"]} == {"신해범", "공이"}
    assert len(transport.calls) == 3
    assert all(call["timeout"] == 90.0 for call in transport.calls)


def test_runner_runs_timeout_rescue_halves_sequentially() -> None:
    body_1 = _ok_body('{"src":"신해범","dst":"申海范","type":"男性角色"}\n')
    body_2 = _ok_body('{"src":"공이","dst":"孔二","type":"性别未知/不适用"}\n')
    transport = DelayedTransport(
        responses=[
            TimeoutError("ReadTimeout"),
            TransportResult(200, body_1),
            TransportResult(200, body_2),
        ]
    )
    model = replace(_fast_retry_model(), concurrency_limit=60, timeout_seconds=600.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert {entry["src"] for entry in payload["entries"]} == {"신해범", "공이"}
    assert transport.max_active == 1


def test_runner_caps_timeout_for_glossary_requests() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = replace(_model(), concurrency_limit=60, timeout_seconds=600.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.calls[0]["timeout"] == 90.0


def test_runner_respects_user_timeout_below_glossary_soft_cap() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = replace(_model(), timeout_seconds=30.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.calls[0]["timeout"] == 30.0


def test_runner_does_not_preemptively_split_long_high_concurrency_glossary_chunk() -> None:
    body = _ok_body(
        '{"src":"신해범","dst":"申海范","type":"男性角色"}\n'
        '{"src":"공이","dst":"孔二","type":"性别未知/不适用"}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = replace(_model(), concurrency_limit=60, timeout_seconds=600.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )
    lines = [f"신해범 and 공이 line {index}" for index in range(120)]
    chunk = GlossaryChunk(
        chunk_id="chunk-long",
        source_file=Path("/in/Novel.txt"),
        text="\n".join(lines),
    )

    result = asyncio.run(
        runner.run(
            Subtask(
                id=chunk.chunk_id,
                task_id="t1",
                request_payload=encode_glossary_payload(chunk),
            )
        )
    )

    payload = json.loads(result.response_content)
    assert {entry["src"] for entry in payload["entries"]} == {"신해범", "공이"}
    assert len(transport.calls) == 1
    assert all(call["timeout"] == 90.0 for call in transport.calls)
    prompt = transport.calls[0]["payload"]["messages"][0]["content"]
    assert "line 0" in prompt
    assert "line 119" in prompt


def test_runner_does_not_recursively_split_glossary_timeout_rescue() -> None:
    transport = FakeTransport(
        responses=[
            TimeoutError("ReadTimeout"),
            TimeoutError("ReadTimeout"),
        ]
    )
    model = replace(_fast_retry_model(), concurrency_limit=60, timeout_seconds=600.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    with pytest.raises(Exception) as caught:
        asyncio.run(runner.run(_make_subtask()))

    assert "ReadTimeout" in str(caught.value)
    assert len(transport.calls) == 2
    assert all(call["timeout"] == 90.0 for call in transport.calls)


def test_runner_caps_user_timeout_for_low_concurrency_glossary_requests() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = replace(_model(), concurrency_limit=1, timeout_seconds=600.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.calls[0]["timeout"] == 90.0


def test_runner_caps_user_timeout_at_old_high_concurrency_threshold() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"男性角色"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    model = replace(_model(), concurrency_limit=20, timeout_seconds=600.0)
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=model,
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.calls[0]["timeout"] == 90.0


def test_runner_accepts_entries_with_decode_issues_without_retry() -> None:
    bad_body = _ok_body(
        "好的，分析如下：\n"
        '{"src":"유찬","dst":"刘灿","type":"男性角色"}\n'
        '{"src":"은빛","dst":"银光","type":"女性角色"}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, bad_body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    assert len(transport.calls) == 1
    payload = json.loads(result.response_content)
    assert payload["issues"]
    assert {entry["src"] for entry in payload["entries"]} == {"유찬", "은빛"}


def test_runner_accepts_entries_with_empty_info_as_decode_issues() -> None:
    bad_body = _ok_body(
        '{"src":"유찬","dst":"刘灿","type":""}\n'
        '{"src":"은빛","dst":"银光","type":" "}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, bad_body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    assert len(transport.calls) == 1
    payload = json.loads(result.response_content)
    assert [entry["info"] for entry in payload["entries"]] == ["", ""]
    assert len(payload["issues"]) == 2


def test_runner_returns_partial_attempt_without_quality_retries() -> None:
    partial_body = _ok_body(
        '{"src":"유찬","dst":"刘灿","type":""}\n'
        '{"src":"은빛","dst":"银光","type":"女性角色"}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, partial_body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    assert len(transport.calls) == 1
    payload = json.loads(result.response_content)
    assert {entry["src"] for entry in payload["entries"]} == {"유찬", "은빛"}
    assert any("missing or empty" in issue["reason"] for issue in payload["issues"])


def test_runner_returns_zero_entries_after_format_retries_exhausted() -> None:
    """If every attempt comes back as off-spec markdown without a
    header, retries exhaust and the runner returns the last attempt's
    payload (zero entries + issues) — the chunk goes red in the UI but
    the run continues."""

    bad_body = _ok_body(
        "好的，分析如下：\n"
        "| 男性角色 | 미아 | 米亚 | 主角 |\n"
        "| 男性角色 | 로건 | 罗根 | 父亲 |\n"
        "| 命名地理 | 벨로리아 | 贝洛利亚 | 王国 |\n"
        "| 命名地理 | 인간계 | 人间界 | 人类世界 |\n"
    )
    transport = FakeTransport(
        responses=[TransportResult(200, bad_body) for _ in range(2)]
    )
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_fast_retry_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    assert len(transport.calls) == 2  # one format repair attempt
    payload = json.loads(result.response_content)
    assert payload["entries"] == []
    assert payload["issues"]


def test_runner_does_not_retry_when_chunk_legitimately_has_no_terms() -> None:
    """An empty/short response with no decode issues means the LLM
    legitimately found nothing extractable. Retrying would burn tokens
    for no reason — only retry on format-failure (issues >= threshold)."""

    body = _ok_body("")
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    assert len(transport.calls) == 1  # no retry
    payload = json.loads(result.response_content)
    assert payload["entries"] == []


def test_decode_glossary_subtask_response_handles_empty_input() -> None:
    assert decode_glossary_subtask_response("") == ((), ())
    assert decode_glossary_subtask_response("not json") == ((), ())
