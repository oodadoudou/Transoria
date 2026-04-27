from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from transoria.domain import Language, TaskStatus
from transoria.formats.epub_parser import parse_epub_file
from transoria.llm import LlmClient, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import TaskCache
from transoria.workflows.translation import (
    BILINGUAL_OUTPUT_FOLDER_EN,
    Glossary,
    GlossaryEntry,
    STATISTICS_FILENAME_JSON,
    TranslationConfig,
    TranslationOrchestrator,
)
from tests.test_formats_epub_parser import _write_minimal_epub


_PREFIX = "翻译:"


@dataclass
class EchoTranslateTransport:
    """Fake transport that "translates" each JSONL line by prefixing it.

    The orchestrator's chunking, runner, and decoding are exercised end-to-end;
    only the network call is replaced.
    """

    requests: list[dict[str, object]] = field(default_factory=list)
    forced_failure_chunks: tuple[int, ...] = ()

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests.append(dict(payload))
        chunk_index = len(self.requests) - 1
        if chunk_index in self.forced_failure_chunks:
            return TransportResult(500, {"error": "boom"})

        user_message = payload["messages"][-1]["content"]
        translate_section = user_message.rsplit("[Translate]\n", 1)[-1]

        lines: list[str] = []
        for line in translate_section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = json.loads(stripped)
            for key, value in parsed.items():
                lines.append(
                    json.dumps({key: f"{_PREFIX}{value}"}, ensure_ascii=False)
                )
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        return TransportResult(200, body)


def _model(retry_attempts: int = 2) -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("key",),
        thinking_level=ThinkingLevel.OFF,
        concurrency_limit=2,
        rpm_limit=0,
        retry_attempts=retry_attempts,
        retry_initial_backoff_seconds=0.0,
        retry_max_backoff_seconds=0.0,
    )


_FROZEN_TIMES = iter([f"2026-04-27T00:00:{n:02d}+00:00" for n in range(60)])


def _frozen_clock() -> str:
    return next(_FROZEN_TIMES, "2026-04-27T00:00:59+00:00")


def _build_config(
    *,
    input_dir: Path,
    output_dir: Path,
    bilingual: bool = False,
    glossary: Glossary | None = None,
    chunk_size: int = 4,
    retry_attempts: int = 2,
    failed_chunk_split_rounds: int = 3,
) -> TranslationConfig:
    return TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=_model(retry_attempts=retry_attempts),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=glossary or Glossary.empty(),
        bilingual_enabled=bilingual,
        chunk_size=chunk_size,
        context_line_count=2,
        failed_chunk_split_rounds=failed_chunk_split_rounds,
    )


def _new_orchestrator(transport: EchoTranslateTransport, cache_root: Path) -> TranslationOrchestrator:
    counter = iter(range(1000))

    def id_factory() -> str:
        return f"task-{next(counter):04d}"

    return TranslationOrchestrator(
        cache=TaskCache(root=cache_root),
        client=LlmClient(transport=transport),
        clock=lambda: f"2026-04-27T00:00:{next(_TIMES):02d}+00:00",
        id_factory=id_factory,
    )


_TIMES = iter(range(60))


def test_orchestrator_translates_txt_file_end_to_end(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    cache_root = tmp_path / "cache"
    input_dir.mkdir()

    source = input_dir / "Sample Novel.txt"
    source.write_text("첫 줄\n둘째 줄\n\n셋째 줄\n", encoding="utf-8")

    transport = EchoTranslateTransport()
    orchestrator = _new_orchestrator(transport, cache_root)

    result = asyncio.run(
        orchestrator.run(_build_config(input_dir=input_dir, output_dir=output_dir))
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert len(result.translated_outputs) == 1
    translated_path = result.translated_outputs[0]
    assert translated_path.name == "Sample Novel-zh.txt"
    body = translated_path.read_text(encoding="utf-8")
    # The blank line is preserved; the three non-empty lines are translated.
    assert body.count(_PREFIX) == 3
    assert "翻译:첫 줄" in body
    assert "\n\n" in body
    # Statistics file exists and has the right counts.
    stats_path = output_dir / STATISTICS_FILENAME_JSON
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    assert stats["completed_segments"] == 3
    assert stats["total_segments"] == 3
    assert stats["failed_subtasks"] == 0
    assert stats["input_tokens"] >= 10
    assert stats["output_tokens"] >= 20


def test_orchestrator_writes_bilingual_output_under_shared_subfolder(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Novel.txt").write_text("원문\n", encoding="utf-8")

    transport = EchoTranslateTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                bilingual=True,
            )
        )
    )

    assert len(result.bilingual_outputs) == 1
    bilingual_path = result.bilingual_outputs[0]
    assert bilingual_path.parent.name == BILINGUAL_OUTPUT_FOLDER_EN
    assert bilingual_path.name == "Novel-zh-kr.txt"
    body = bilingual_path.read_text(encoding="utf-8")
    assert "원문" in body
    assert f"{_PREFIX}원문" in body


def test_orchestrator_dedupes_bilingual_when_source_equals_translation(tmp_path: Path) -> None:
    """If source == translation (e.g., a proper noun line that the LLM left
    unchanged), the bilingual writer must not duplicate the line when the
    setting is on."""

    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Echo.txt").write_text("Hello\n", encoding="utf-8")

    @dataclass
    class IdentityTransport:
        async def execute(
            self,
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, object],
            timeout: float,
        ) -> TransportResult:
            user_message = payload["messages"][-1]["content"]
            translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
            lines: list[str] = []
            for line in translate_section.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                parsed = json.loads(stripped)
                for key, value in parsed.items():
                    # Echo unchanged.
                    lines.append(
                        json.dumps({key: value}, ensure_ascii=False)
                    )
            body = {
                "choices": [
                    {"message": {"role": "assistant", "content": "\n".join(lines)}}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
            return TransportResult(200, body)

    transport = IdentityTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")  # type: ignore[arg-type]

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir, output_dir=output_dir, bilingual=True
            )
        )
    )

    bilingual_path = result.bilingual_outputs[0]
    body = bilingual_path.read_text(encoding="utf-8")

    # Only one "Hello" line, not two.
    assert body.count("Hello") == 1


def test_orchestrator_records_failed_subtasks_in_statistics(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Two.txt").write_text(
        "\n".join(f"line {n}" for n in range(8)) + "\n", encoding="utf-8"
    )

    # chunk_size=4 → two chunks. Force the second chunk to fail.
    transport = EchoTranslateTransport(forced_failure_chunks=(1,))
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                chunk_size=4,
                retry_attempts=0,
                failed_chunk_split_rounds=0,
            )
        )
    )

    assert result.final_status is TaskStatus.FAILED
    assert result.statistics.failed_subtasks == 1
    # The statistics file records the partially-translated file as failed.
    assert any(item.path.endswith("Two.txt") for item in result.statistics.failed_files)


def test_orchestrator_splits_failed_chunk_and_retries_children(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Split.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")

    @dataclass
    class FailLargeChunkTransport:
        request_line_counts: list[int] = field(default_factory=list)

        async def execute(
            self,
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, object],
            timeout: float,
        ) -> TransportResult:
            user_message = payload["messages"][-1]["content"]
            translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
            rows = [line for line in translate_section.splitlines() if line.strip()]
            self.request_line_counts.append(len(rows))
            if len(rows) > 2:
                return TransportResult(500, {"error": "chunk too large"})
            lines: list[str] = []
            for line in rows:
                parsed = json.loads(line)
                for key, value in parsed.items():
                    lines.append(json.dumps({key: f"{_PREFIX}{value}"}, ensure_ascii=False))
            return TransportResult(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "\n".join(lines)}}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

    transport = FailLargeChunkTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")  # type: ignore[arg-type]

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                chunk_size=4,
                retry_attempts=0,
            )
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert transport.request_line_counts == [4, 2, 2]
    assert result.statistics.failed_subtasks == 0
    assert result.statistics.completed_segments == 4
    assert result.translated_outputs[0].read_text(encoding="utf-8").count(_PREFIX) == 4
    snapshot = orchestrator.cache.load(result.task_id)
    skipped = [s for s in snapshot.subtasks if s.status.value == "skipped"]
    children = [s for s in snapshot.subtasks if s.request_payload.get("parent_subtask_id")]
    assert len(skipped) == 1
    assert len(children) == 2


def test_orchestrator_keeps_single_line_split_failure_as_final_failure(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Single.txt").write_text("a\n", encoding="utf-8")

    transport = EchoTranslateTransport(forced_failure_chunks=(0,))
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                chunk_size=1,
                retry_attempts=0,
            )
        )
    )

    assert result.final_status is TaskStatus.FAILED
    assert result.statistics.failed_subtasks == 1


def test_orchestrator_translates_epub_file_and_preserves_structure(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    epub_source = _write_minimal_epub(input_dir / "Korean.epub")

    transport = EchoTranslateTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(
            _build_config(input_dir=input_dir, output_dir=output_dir, chunk_size=8)
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    epub_outputs = [path for path in result.translated_outputs if path.suffix == ".epub"]
    assert len(epub_outputs) == 1
    out_path = epub_outputs[0]
    assert out_path.name == "Korean-zh.epub"

    # Re-parse the translated EPUB and verify body segments now carry the prefix.
    out_doc = parse_epub_file(out_path)
    body_texts = [seg.text for seg in out_doc.segments if seg.kind.value == "body"]
    assert body_texts
    assert all(_PREFIX in text for text in body_texts)

    # The original EPUB was not modified.
    src_doc = parse_epub_file(epub_source)
    assert all(_PREFIX not in seg.text for seg in src_doc.segments)


def test_orchestrator_passes_glossary_only_when_matched(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Names.txt").write_text(
        "신해범 walked into the room.\nJust a plain line.\n", encoding="utf-8"
    )

    glossary = Glossary(
        entries=(
            GlossaryEntry(src="신해범", dst="申海范", info="Male Name"),
            GlossaryEntry(src="흑룡", dst="黑龙", info="Creature"),
        )
    )
    transport = EchoTranslateTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")

    asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                glossary=glossary,
                chunk_size=8,
            )
        )
    )

    # The single chunk should contain a [Glossary] section that lists 신해범
    # but not 흑룡 (which never appears in the source).
    user_message = transport.requests[0]["messages"][-1]["content"]
    assert "[Glossary]" in user_message
    assert "신해범" in user_message
    assert "흑룡" not in user_message


def test_orchestrator_returns_completed_status_for_empty_input_dir(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()

    transport = EchoTranslateTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(_build_config(input_dir=input_dir, output_dir=output_dir))
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert result.translated_outputs == ()
    assert result.statistics.total_segments == 0
