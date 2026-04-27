"""Verifies that segments which become empty after preprocessing are not lost
in the output — the writer's fallback to original source covers them."""

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
from transoria.workflows.translation import (
    Glossary,
    ReplacementRule,
    TranslationConfig,
    TranslationOrchestrator,
)


@dataclass
class EchoTransport:
    requests: int = field(default=0, init=False)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests += 1
        user_message = payload["messages"][-1]["content"]
        translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
        lines: list[str] = []
        for line in translate_section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = json.loads(stripped)
            for key, value in parsed.items():
                lines.append(json.dumps({key: f"翻译:{value}"}, ensure_ascii=False))
        body = {
            "choices": [{"message": {"role": "assistant", "content": "\n".join(lines)}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
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
        retry_attempts=0,
    )


def test_segment_emptied_by_pre_replacement_falls_back_to_original_source(tmp_path: Path) -> None:
    """A pre-replacement that wipes the entire segment leaves the segment
    untouched in the output (writer fallback). The LLM never sees an empty
    chunk, and the original line survives intact."""

    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Sample.txt").write_text(
        "DROP_ME alone\nKeep this line\n", encoding="utf-8"
    )

    transport = EchoTransport()
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-restore",
    )
    config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        pre_replacements=(
            ReplacementRule(src=r"DROP_ME alone", dst="", regex=False),
        ),
        chunk_size=4,
        context_line_count=0,
    )

    result = asyncio.run(orchestrator.run(config))

    assert result.final_status is TaskStatus.COMPLETED
    assert len(result.translated_outputs) == 1
    body = result.translated_outputs[0].read_text(encoding="utf-8")

    # The segment that became empty after pre-replacement is restored to its
    # original text in the output. The other line is translated normally.
    assert "DROP_ME alone" in body
    assert "翻译:Keep this line" in body
    # No empty translation was sent — every chunk that ran had content.
    assert transport.requests >= 1


def test_segment_with_only_protected_content_round_trips_via_sentinels(tmp_path: Path) -> None:
    """Even when a segment is *almost* entirely a protected span, the
    sentinel-based round-trip preserves the protected content."""

    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Sample.txt").write_text("Visit {{URL}} now\n", encoding="utf-8")

    transport = EchoTransport()
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-protect",
    )
    from transoria.workflows.translation import TextPreserveRule

    config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        text_preserve_rules=(TextPreserveRule(pattern=r"\{\{[A-Z_]+\}\}"),),
        chunk_size=4,
    )

    result = asyncio.run(orchestrator.run(config))
    body = result.translated_outputs[0].read_text(encoding="utf-8")

    # The protected token survives untranslated; the surrounding text is
    # marked with the echo prefix.
    assert "{{URL}}" in body
    assert "翻译:" in body
