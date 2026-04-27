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
    TranslationConfig,
    TranslationOrchestrator,
    evaluate_segment_confidence,
)


def test_evaluate_flags_excessive_length_inflation() -> None:
    verdict = evaluate_segment_confidence(
        "short",
        "x" * 100,
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert verdict.is_low_confidence
    assert any("length ratio" in reason for reason in verdict.reasons)


def test_evaluate_flags_excessive_truncation() -> None:
    verdict = evaluate_segment_confidence(
        "this is a substantial source line",
        "ok",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert verdict.is_low_confidence


def test_evaluate_flags_punctuation_delta() -> None:
    verdict = evaluate_segment_confidence(
        "first sentence. second one. third! and fourth?",
        "single output sentence with no terminators",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=2,
    )

    assert verdict.is_low_confidence
    assert any("punctuation" in reason for reason in verdict.reasons)


def test_evaluate_passes_normal_translation() -> None:
    verdict = evaluate_segment_confidence(
        "신해범 walked into the room.",
        "申海范走进了房间。",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert not verdict.is_low_confidence


def test_evaluate_skips_when_either_side_blank() -> None:
    assert not evaluate_segment_confidence(
        "",
        "translated",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    ).is_low_confidence


def test_evaluate_flags_empty_translation_for_nonempty_source() -> None:
    verdict = evaluate_segment_confidence(
        "This line needs translation.",
        "",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert verdict.is_low_confidence
    assert any("empty translation" in reason for reason in verdict.reasons)


def test_evaluate_flags_korean_residue_when_source_is_korean() -> None:
    verdict = evaluate_segment_confidence(
        "신해범이 방에 들어왔다.",
        "신해범 walked into the room.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.KOREAN,
    )

    assert verdict.is_low_confidence
    assert any("Korean residue" in reason for reason in verdict.reasons)


def test_evaluate_flags_japanese_kana_residue_when_source_is_japanese() -> None:
    verdict = evaluate_segment_confidence(
        "彼は部屋に入った。",
        "彼は entered the room.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.JAPANESE,
    )

    assert verdict.is_low_confidence
    assert any("Japanese kana residue" in reason for reason in verdict.reasons)


def test_evaluate_flags_identical_source_and_translation() -> None:
    verdict = evaluate_segment_confidence(
        "This should not come back unchanged.",
        "This should not come back unchanged.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.ENGLISH,
    )

    assert verdict.is_low_confidence
    assert any("too similar" in reason for reason in verdict.reasons)


@dataclass
class TruncatingTransport:
    """Returns a translation that's clearly too short to pass length check."""

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
        translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
        lines: list[str] = []
        for line in translate_section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = json.loads(stripped)
            for key, _value in parsed.items():
                # Always return the same single character — way below the
                # configured min_length_ratio so it's flagged.
                lines.append(json.dumps({key: "x"}, ensure_ascii=False))
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return TransportResult(200, body)


def test_orchestrator_records_low_confidence_segments_in_statistics(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()

    (input_dir / "Sample.txt").write_text(
        "This is a meaningfully long source line.\n"
        "Another reasonably long source line!\n",
        encoding="utf-8",
    )

    transport = TruncatingTransport()
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-conf",
    )
    config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="model-x",
            api_keys=("key",),
            thinking_level=ThinkingLevel.OFF,
            rpm_limit=0,
            retry_attempts=0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        chunk_size=4,
        context_line_count=0,
        enable_confidence_check=True,
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    result = asyncio.run(orchestrator.run(config))

    assert result.final_status is TaskStatus.COMPLETED
    assert len(result.statistics.low_confidence_segments) == 2
    stats = json.loads(result.statistics_path.read_text(encoding="utf-8"))
    assert len(stats["low_confidence_segments"]) == 2
    for record in stats["low_confidence_segments"]:
        assert record["segment_id"]
        assert record["reasons"]


def test_orchestrator_does_not_record_when_confidence_check_disabled(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Sample.txt").write_text(
        "Long meaningful source line.\n", encoding="utf-8"
    )

    transport = TruncatingTransport()
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-noconf",
    )
    config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="model-x",
            api_keys=("key",),
            rpm_limit=0,
            retry_attempts=0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        chunk_size=4,
        context_line_count=0,
        enable_confidence_check=False,
    )

    result = asyncio.run(orchestrator.run(config))

    assert result.statistics.low_confidence_segments == ()
