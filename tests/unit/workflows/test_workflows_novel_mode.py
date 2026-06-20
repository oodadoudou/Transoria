from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from transoria.domain import Language, TaskStatus
from transoria.llm import LlmClient, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import TaskCache
from transoria.workflows.glossary import GlossaryConfig
from transoria.workflows.novel_mode import (
    NovelModeConfig,
    NovelModeOrchestrator,
)
from transoria.workflows.translation import Glossary, TranslationConfig


_PREFIX = "翻译:"


@dataclass
class TwoStageTransport:
    """One transport for both stages.

    Detects the kind of request by inspecting the user prompt: glossary
    extraction sends ``[Source Text]`` while translation sends
    ``[Translate]``. Returns appropriate JSONL for each.
    """

    candidates: tuple[tuple[str, str, str], ...] = (
        ("신해범", "申海范", "Male Name"),
    )
    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests.append(dict(payload))
        user_message = payload["messages"][-1]["content"]
        if "[Translate]" in user_message:
            translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
            lines: list[str] = []
            for line in translate_section.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if not stripped.startswith("{"):
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                for key, value in parsed.items():
                    lines.append(
                        json.dumps({key: f"{_PREFIX}{value}"}, ensure_ascii=False)
                    )
            body = {
                "choices": [
                    {"message": {"role": "assistant", "content": "\n".join(lines)}}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
            }
            return TransportResult(200, body)

        # Glossary extraction
        lines = [
            json.dumps({"src": s, "dst": d, "type": i}, ensure_ascii=False)
            for s, d, i in self.candidates
        ]
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 13},
        }
        return TransportResult(200, body)


def _model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="m",
        api_keys=("k",),
        thinking_level=ThinkingLevel.OFF,
        rpm_limit=0,
    )


def test_novel_mode_runs_glossary_then_translation_with_extracted_terms(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Sample.txt").write_text(
        "신해범 walked into the room.\nAnother line without names.\n",
        encoding="utf-8",
    )

    transport = TwoStageTransport()
    orchestrator = NovelModeOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
    )

    glossary_config = GlossaryConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        chunk_char_limit=4000,
        min_frequency=1,
    )
    translation_config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        chunk_size=8,
        context_line_count=0,
    )

    result = asyncio.run(
        orchestrator.run(
            NovelModeConfig(
                glossary=glossary_config, translation=translation_config
            )
        )
    )

    assert result.glossary.final_status is TaskStatus.COMPLETED
    assert result.translation is not None
    assert result.translation.final_status is TaskStatus.COMPLETED
    assert result.used_extracted_glossary is True

    # Translation chunk's user prompt must include the extracted glossary entry.
    translation_request = next(
        request
        for request in transport.requests
        if "[Translate]" in request["messages"][-1]["content"]
    )
    assert "신해범 -> 申海范" in translation_request["messages"][-1]["content"]
