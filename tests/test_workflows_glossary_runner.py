from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

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
    responses: list[TransportResult] = field(default_factory=list)
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
        '{"src":"신해범","dst":"申海范","type":"Male Name"}\n'
        '{"src":"공이","dst":"孔二","type":"Author"}\n'
        '{"src":"흑룡","dst":"黑龙","type":"Creature"}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
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
        '{"src":"신해범","dst":"申海范","type":"Male Name"}\n'
        "garbage that cannot be parsed\n"
        '{"src":"","dst":"missing src","type":"x"}\n'
    )
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    result = asyncio.run(runner.run(_make_subtask()))

    payload = json.loads(result.response_content)
    assert {entry["src"] for entry in payload["entries"]} == {"신해범"}
    assert payload["issues"]


def test_runner_passes_source_text_section_in_user_prompt() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"t"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    user_message = transport.calls[0]["payload"]["messages"][-1]["content"]
    assert user_message.startswith("[Source Text]")
    assert "신해범" in user_message


def test_runner_injects_first_name_only_in_prompt() -> None:
    body = _ok_body('{"src":"신해범","dst":"申海范","type":"角色"}\n')
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
    body = _ok_body('{"src":"蓝霁云","dst":"申海范","type":"角色"}\n')
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


def test_runner_includes_thinking_block_when_model_thinking_enabled() -> None:
    body = _ok_body('{"src":"x","dst":"y","type":"t"}\n')
    transport = FakeTransport(responses=[TransportResult(200, body)])
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(thinking=ThinkingLevel.MEDIUM),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    system_message = transport.calls[0]["payload"]["messages"][0]["content"]
    assert "<why>" in system_message
    assert transport.calls[0]["payload"]["thinking"] == {
        "type": "enabled",
        "effort": "medium",
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


def test_decode_glossary_subtask_response_handles_empty_input() -> None:
    assert decode_glossary_subtask_response("") == ((), ())
    assert decode_glossary_subtask_response("not json") == ((), ())
