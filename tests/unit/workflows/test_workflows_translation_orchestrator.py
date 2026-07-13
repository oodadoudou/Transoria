from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

import pytest

from transoria.domain import Language, SubtaskStatus, TaskKind, TaskStatus
from transoria.formats.epub_parser import parse_epub_file
from transoria.llm import LlmClient, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask, SubtaskResult, TaskCache, TaskRecord
from transoria.workflows.translation import (
    BILINGUAL_OUTPUT_FOLDER_EN,
    Glossary,
    GlossaryEntry,
    STATISTICS_FILENAME_JSON,
    TranslationConfig,
    TranslationOrchestrator,
)
from transoria.workflows.translation.orchestrator import (
    _default_runner_factory,
    _should_split_failed_subtask,
    _split_failed_payload,
)
from tests.unit.formats.test_formats_epub_parser import _write_minimal_epub


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
            # Tolerate non-JSON lines (e.g. trailing contract reminders
            # appended after the JSONL body) so the transport stays
            # usable as the runner's prompt structure evolves.
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
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        return TransportResult(200, body)


def _model() -> ModelConfig:
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
    enable_confidence_check: bool = False,
    low_confidence_max_retries: int = 0,
    request_retry_attempts: int = 0,
) -> TranslationConfig:
    return TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=glossary or Glossary.empty(),
        bilingual_enabled=bilingual,
        chunk_size=chunk_size,
        context_line_count=2,
        enable_confidence_check=enable_confidence_check,
        low_confidence_max_retries=low_confidence_max_retries,
        request_retry_attempts=request_retry_attempts,
    )


def _new_orchestrator(transport: EchoTranslateTransport, cache_root: Path) -> TranslationOrchestrator:
    counter = iter(range(1000))

    def id_factory() -> str:
        return f"task-{next(counter):04d}"

    return TranslationOrchestrator(
        cache=TaskCache(root=cache_root),
        client=LlmClient(transport=transport),
        clock=lambda: f"2026-04-27T00:00:{next(_TIMES, 59):02d}+00:00",
        id_factory=id_factory,
    )


_TIMES = iter(range(60))


def test_default_translation_runner_uses_config_request_retry_attempts(tmp_path: Path) -> None:
    config = _build_config(
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        request_retry_attempts=4,
    )

    runner = _default_runner_factory(None, config)  # type: ignore[arg-type]

    assert runner.transport_retry_attempts == 4  # type: ignore[attr-defined]


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
    # Statistics file lives in the central cache (not output) so it survives
    # clean COMPLETED runs and feeds the proofreading flow.
    stats_path = result.statistics_path
    assert stats_path.name == STATISTICS_FILENAME_JSON
    assert not (output_dir / STATISTICS_FILENAME_JSON).exists()
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    assert stats["completed_segments"] == 3
    assert stats["total_segments"] == 3
    assert stats["failed_subtasks"] == 0
    assert stats["input_tokens"] >= 10
    assert stats["output_tokens"] >= 20


def test_orchestrator_only_sends_source_language_residue_for_mixed_cache(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    cache_root = tmp_path / "cache"
    input_dir.mkdir()

    source = input_dir / "Patched Novel.txt"
    source.write_text(
        "这是已经补好的中文。\n"
        "반대쪽도 걸어 도망가지 못하게 한다.\n"
        "中文里混着 경해수 的残留。\n",
        encoding="utf-8",
    )

    transport = EchoTranslateTransport()
    orchestrator = _new_orchestrator(transport, cache_root)

    result = asyncio.run(
        orchestrator.run(_build_config(input_dir=input_dir, output_dir=output_dir))
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert result.statistics.total_segments == 2
    translated = result.translated_outputs[0].read_text(encoding="utf-8")
    assert "这是已经补好的中文。" in translated
    assert "翻译:반대쪽도 걸어 도망가지 못하게 한다." in translated
    assert "翻译:中文里混着 경해수 的残留。" in translated

    sent = str(transport.requests[0]["messages"][-1]["content"])
    assert "这是已经补好的中文。" not in sent
    assert "반대쪽도 걸어 도망가지 못하게 한다." in sent
    assert "中文里混着 경해수 的残留。" in sent


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
                if not stripped.startswith("{"):
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
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


def test_orchestrator_records_failed_subtasks_in_statistics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Two.txt").write_text(
        "\n".join(f"line {n}" for n in range(8)) + "\n", encoding="utf-8"
    )

    # Disable split so this test isolates "failed subtask → statistics".
    monkeypatch.setattr(
        "transoria.workflows.translation.orchestrator._SPLIT_ROUNDS", 0
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
            )
        )
    )

    assert result.final_status is TaskStatus.FAILED
    assert result.statistics.failed_subtasks == 1
    # Partial output is written on FAILED-with-some-completed: the first
    # chunk's 4 translated lines land in the output file; the second
    # chunk's 4 lines fall back to their original source text. The
    # ``failed_files`` entry still flags the file as incomplete so the
    # user can rerun via ``continue_task``.
    assert len(result.translated_outputs) == 1
    translated_path = result.translated_outputs[0]
    assert translated_path.name == "Two-zh.txt"
    body = translated_path.read_text(encoding="utf-8")
    assert body.count(_PREFIX) == 4
    for n in range(4, 8):
        assert f"line {n}" in body
    failed_file = next(
        item
        for item in result.statistics.failed_files
        if item.path.endswith("Two.txt")
    )
    assert failed_file.code == "missing_translations"
    assert failed_file.details == {"missing_segments": 4}
    assert result.bilingual_outputs == ()
    assert not (output_dir / BILINGUAL_OUTPUT_FOLDER_EN).exists()


def test_orchestrator_writes_no_outputs_when_stopped(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Stopped.txt").write_text("a\nb\n", encoding="utf-8")
    holder: dict[str, object] = {}

    class StopAwareRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            while True:
                executor = holder.get("executor")
                if executor is not None and getattr(executor, "is_stopping", False):
                    break
                await asyncio.sleep(0)
            return SubtaskResult(response_content="")

    def request_stop(_event: object) -> None:
        executor = holder.get("executor")
        if executor is not None:
            executor.request_stop()  # type: ignore[attr-defined]

    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=EchoTranslateTransport()),
        runner_factory=lambda _client, _config: StopAwareRunner(),
        progress=request_stop,
        on_executor_created=lambda executor: holder.__setitem__("executor", executor),
        id_factory=lambda: "task-stopped",
    )

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                chunk_size=1,
            )
        )
    )

    assert result.final_status is TaskStatus.STOPPED
    assert result.translated_outputs == ()
    assert result.bilingual_outputs == ()
    assert not list(output_dir.glob("*-zh.txt"))
    assert not (output_dir / BILINGUAL_OUTPUT_FOLDER_EN).exists()


def test_orchestrator_finalizes_outputs_when_stop_hits_after_all_segments(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Finished.txt").write_text("a\nb\n", encoding="utf-8")
    holder: dict[str, object] = {}

    class CompleteRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            translations = {
                str(segment["segment_id"]): f"{_PREFIX}{segment['prompt_text']}"
                for segment in subtask.request_payload["segments"]
            }
            return SubtaskResult(
                response_content=json.dumps(
                    {
                        "version": 2,
                        "translations": translations,
                        "low_confidence": [],
                    },
                    ensure_ascii=False,
                )
            )

    def request_stop(_event: object) -> None:
        executor = holder.get("executor")
        if executor is not None:
            executor.request_stop()  # type: ignore[attr-defined]

    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=EchoTranslateTransport()),
        runner_factory=lambda _client, _config: CompleteRunner(),
        progress=request_stop,
        on_executor_created=lambda executor: holder.__setitem__("executor", executor),
        id_factory=lambda: "task-stop-after-complete",
    )

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                chunk_size=2,
            )
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert len(result.translated_outputs) == 1
    assert result.translated_outputs[0].read_text(encoding="utf-8").count(_PREFIX) == 2
    assert orchestrator.cache.load(result.task_id).record.status is TaskStatus.COMPLETED
    assert getattr(holder["executor"], "subtask_timeout_seconds") == 0.0


def test_orchestrator_does_not_split_empty_response_failure(tmp_path: Path) -> None:
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
            rows = [
                line
                for line in translate_section.splitlines()
                if line.strip().startswith("{")
            ]
            self.request_line_counts.append(len(rows))
            if len(rows) > 2:
                return TransportResult(
                    200,
                    {
                        "choices": [{"message": {"role": "assistant", "content": ""}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 0},
                    },
                )
            lines: list[str] = []
            for line in rows:
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
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
            )
        )
    )

    assert result.final_status is TaskStatus.FAILED
    assert transport.request_line_counts == [4, 4, 4]
    assert result.statistics.failed_subtasks == 1
    assert result.statistics.completed_segments == 0
    assert result.translated_outputs == ()
    snapshot = orchestrator.cache.load(result.task_id)
    skipped = [s for s in snapshot.subtasks if s.status.value == "skipped"]
    children = [s for s in snapshot.subtasks if s.request_payload.get("parent_subtask_id")]
    assert skipped == []
    assert children == []


def test_orchestrator_marks_mass_source_echo_for_model_switch_recovery(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    sources = [f"안녕하세요 친구입니다 {idx}" for idx in range(4)]
    (input_dir / "Echo.txt").write_text("\n".join(sources) + "\n", encoding="utf-8")

    @dataclass
    class EchoLargeChunkTransport:
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
            rows = [
                line
                for line in translate_section.splitlines()
                if line.strip().startswith("{")
            ]
            self.request_line_counts.append(len(rows))
            lines: list[str] = []
            for line in rows:
                parsed = json.loads(line)
                for key, value in parsed.items():
                    translated = value if len(rows) > 2 else f"中文{key}"
                    lines.append(json.dumps({key: translated}, ensure_ascii=False))
            return TransportResult(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "\n".join(lines)}}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

    transport = EchoLargeChunkTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")  # type: ignore[arg-type]

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                chunk_size=4,
                enable_confidence_check=True,
                low_confidence_max_retries=0,
            )
        )
    )

    assert result.final_status is TaskStatus.FAILED
    assert result.statistics.failed_subtasks == 0
    assert transport.request_line_counts == [4, 4]
    body = result.translated_outputs[0].read_text(encoding="utf-8")
    for source in sources:
        assert source in body
    snapshot = orchestrator.cache.load(result.task_id)
    skipped = [s for s in snapshot.subtasks if s.status.value == "skipped"]
    children = [s for s in snapshot.subtasks if s.request_payload.get("parent_subtask_id")]
    assert skipped == []
    assert children == []


def test_orchestrator_fails_persistent_source_echo_for_model_switch_recovery(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    sources = [f"안녕하세요 친구입니다 {idx}" for idx in range(4)]
    (input_dir / "Echo.txt").write_text("\n".join(sources) + "\n", encoding="utf-8")

    @dataclass
    class EchoEveryRequestTransport:
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
            rows = [
                line
                for line in translate_section.splitlines()
                if line.strip().startswith("{")
            ]
            self.request_line_counts.append(len(rows))
            lines: list[str] = []
            for line in rows:
                parsed = json.loads(line)
                for key, value in parsed.items():
                    lines.append(json.dumps({key: value}, ensure_ascii=False))
            return TransportResult(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "\n".join(lines)}}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

    transport = EchoEveryRequestTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")  # type: ignore[arg-type]

    result = asyncio.run(
        orchestrator.run(
            _build_config(
                input_dir=input_dir,
                output_dir=output_dir,
                chunk_size=4,
                enable_confidence_check=True,
                low_confidence_max_retries=1,
            )
        )
    )

    assert result.final_status is TaskStatus.FAILED
    assert len(result.translated_outputs) == 1
    assert result.statistics.failed_subtasks == 0
    assert transport.request_line_counts[0] == 4
    assert len(transport.request_line_counts) >= 2
    body = result.translated_outputs[0].read_text(encoding="utf-8")
    for source in sources:
        assert source in body
    snapshot = orchestrator.cache.load(result.task_id)
    terminal_failures = [
        s
        for s in snapshot.subtasks
        if s.status.value == "failed"
    ]
    assert terminal_failures == []


def test_continue_with_switched_model_recovers_only_unresolved_segments(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    sources = [f"복구 대상 문장 {idx}" for idx in range(13)]
    (input_dir / "Recovery.txt").write_text(
        "\n".join(sources) + "\n", encoding="utf-8"
    )

    @dataclass
    class SwitchableTransport:
        request_segment_ids: list[list[str]] = field(default_factory=list)

        async def execute(
            self,
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, object],
            timeout: float,
        ) -> TransportResult:
            user_message = payload["messages"][-1]["content"]
            translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
            rows = [
                json.loads(line)
                for line in translate_section.splitlines()
                if line.strip().startswith("{")
            ]
            segment_ids = [str(key) for row in rows for key in row]
            self.request_segment_ids.append(segment_ids)
            lines: list[str] = []
            for row in rows:
                for key in row:
                    translated = f"补救译文-{key}"
                    lines.append(json.dumps({key: translated}, ensure_ascii=False))
            return TransportResult(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "\n".join(lines)}}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

    transport = SwitchableTransport()
    task_id = "translation-segment-recovery"
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),  # type: ignore[arg-type]
        clock=_frozen_clock,
        id_factory=lambda: task_id,
    )
    config = _build_config(
        input_dir=input_dir,
        output_dir=output_dir,
        chunk_size=16,
        enable_confidence_check=True,
        low_confidence_max_retries=0,
    )

    seeded_result = asyncio.run(orchestrator.run(config))
    assert seeded_result.final_status is TaskStatus.COMPLETED
    snapshot = orchestrator.cache.load(task_id)
    assert len(snapshot.subtasks) == 1
    owner = snapshot.subtasks[0]
    fallback_ids = {f"0:{index}" for index in range(1, 7)}
    fallback_payload = {
        "version": 2,
        "translations": {
            f"0:{index}": (
                sources[index]
                if f"0:{index}" in fallback_ids
                else f"初始正常译文-0:{index}"
            )
            for index in range(13)
        },
        "low_confidence": [
            {
                "segment_id": segment_id,
                "reasons": ["fell_back_to_source_after_max_retries"],
                "tags": ["source_residue"],
            }
            for segment_id in sorted(fallback_ids)
        ],
    }
    orchestrator.cache.save_subtask(
        replace(owner, response_content=json.dumps(fallback_payload, ensure_ascii=False))
    )
    orchestrator.cache.save_task(snapshot.record.with_status(TaskStatus.FAILED))
    transport.request_segment_ids.clear()
    switched_config = replace(
        config,
        model=replace(config.model, id="rescue-model", display_name="Rescue model"),
    )

    recovered_result = asyncio.run(orchestrator.run(switched_config))

    assert recovered_result.final_status is TaskStatus.COMPLETED
    assert transport.request_segment_ids == [
        [str(index) for index in range(1, 6)],
        ["6"],
    ]
    recovered_body = recovered_result.translated_outputs[0].read_text(encoding="utf-8")
    for index in (0, *range(7, 13)):
        assert f"初始正常译文-0:{index}" in recovered_body
    for index in range(1, 7):
        assert f"补救译文-{index}" in recovered_body
        assert sources[index] not in recovered_body


def test_split_failed_payload_clears_context_lines() -> None:
    payload = {
        "segments": [
            {"segment_id": "0:0", "chunk_index": 0, "prompt_text": "a"},
            {"segment_id": "0:1", "chunk_index": 1, "prompt_text": "b"},
        ],
        "context_lines": ["previous sentence."],
    }

    children = _split_failed_payload(
        payload, parent_subtask_id="chunk-00000", max_rounds=2
    )

    assert len(children) == 2
    assert all(child["context_lines"] == [] for child in children)


def test_split_failed_subtask_requires_explicit_source_residue() -> None:
    def failed_subtask(last_error: str, response_content: str = "") -> Subtask:
        return Subtask(
            id="chunk-00000",
            task_id="task",
            status=SubtaskStatus.FAILED,
            request_payload={
                "segments": [
                    {"segment_id": "0:0", "chunk_index": 0, "prompt_text": "a"},
                    {"segment_id": "0:1", "chunk_index": 1, "prompt_text": "b"},
                ]
            },
            last_error=last_error,
            response_content=response_content,
        )

    assert not _should_split_failed_subtask(
        failed_subtask("[llm.transport_error] LlmRequestError: timeout")
    )
    assert not _should_split_failed_subtask(
        failed_subtask("[llm.no_api_key] NoApiKeyError: missing")
    )
    assert not _should_split_failed_subtask(
        failed_subtask("[llm.http_error] LlmRequestError: HTTP 400 from provider")
    )
    assert not _should_split_failed_subtask(
        failed_subtask("[llm.http_error] LlmRequestError: HTTP 401 from provider")
    )
    assert not _should_split_failed_subtask(
        failed_subtask("[llm.http_error] LlmRequestError: HTTP 429 from provider")
    )
    assert not _should_split_failed_subtask(
        failed_subtask("[llm.http_error] LlmRequestError: HTTP 503 from provider")
    )
    assert not _should_split_failed_subtask(
        failed_subtask("[llm.line_count_mismatch] LlmRequestError: expected 2 got 1")
    )
    assert not _should_split_failed_subtask(
        failed_subtask(
            "[llm.degenerate_output] LlmDegenerateOutputError: runaway repetition"
        )
    )
    assert not _should_split_failed_subtask(
        failed_subtask("ValueError: malformed translation response")
    )
    assert not _should_split_failed_subtask(
        failed_subtask(
            "[translation.quality_failed] duplicate output",
            json.dumps(
                {
                    "version": 2,
                    "translations": {"0:0": "重复", "0:1": "重复"},
                    "low_confidence": [
                        {
                            "segment_id": "0:0",
                            "reasons": ["possible duplicate"],
                            "tags": ["possible_duplicate"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
    )
    assert not _should_split_failed_subtask(
        failed_subtask(
            "[translation.quality_failed] source residue warning",
            json.dumps(
                {
                    "version": 2,
                    "translations": {"0:0": "申海范(신해범)"},
                    "low_confidence": [
                        {
                            "segment_id": "0:0",
                            "reasons": ["source language remains"],
                            "tags": ["source_residue"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
    )
    assert _should_split_failed_subtask(
        failed_subtask(
            "[translation.quality_failed] source residue remains",
            json.dumps(
                {
                    "version": 2,
                    "translations": {"0:0": "원문", "0:1": "正常译文"},
                    "low_confidence": [
                        {
                            "segment_id": "0:0",
                            "reasons": ["fell_back_to_source_after_max_retries"],
                            "tags": ["source_residue"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
    )


def test_orchestrator_marks_running_before_source_residue_split(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    orchestrator = _new_orchestrator(EchoTranslateTransport(), tmp_path / "cache")
    task_id = "task-source-residue-split"
    request_segments = [
        {
            "segment_id": f"0:{index}",
            "chunk_index": index,
            "prompt_text": f"원문 {index}",
        }
        for index in range(4)
    ]
    failed = Subtask(
        id="chunk-00000",
        task_id=task_id,
        status=SubtaskStatus.FAILED,
        request_payload={"segments": request_segments},
        response_content=json.dumps(
            {
                "version": 2,
                "translations": {f"0:{index}": f"원문 {index}" for index in range(4)},
                "low_confidence": [
                    {
                        "segment_id": f"0:{index}",
                        "reasons": ["fell_back_to_source_after_max_retries"],
                        "tags": ["source_residue"],
                    }
                    for index in range(4)
                ],
            },
            ensure_ascii=False,
        ),
        last_error="[translation.quality_failed] source residue remains",
    )
    orchestrator.cache.write_seed(
        TaskRecord(id=task_id, kind=TaskKind.TRANSLATION, status=TaskStatus.FAILED),
        [failed],
    )

    created = orchestrator._split_failed_subtasks(
        task_id,
        orchestrator.cache.load_subtasks(task_id),
        _build_config(input_dir=input_dir, output_dir=output_dir),
    )

    assert created == 2
    snapshot = orchestrator.cache.load(task_id)
    assert snapshot.record.status is TaskStatus.RUNNING
    assert len([s for s in snapshot.subtasks if s.status is SubtaskStatus.SKIPPED]) == 1
    assert len(
        [s for s in snapshot.subtasks if s.request_payload.get("parent_subtask_id")]
    ) == 2


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


def test_orchestrator_raises_typed_error_for_empty_input_dir(tmp_path: Path) -> None:
    """Empty input no longer silently completes — it raises
    ``TranslationEmptyInputError`` so ``_on_task_failure`` can record a
    specific reason in ``record.metadata.last_error`` instead of the
    user seeing "completed 0/0" with no explanation."""

    from transoria.workflows.translation.orchestrator import (
        TranslationEmptyInputError,
    )

    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()

    transport = EchoTranslateTransport()
    orchestrator = _new_orchestrator(transport, tmp_path / "cache")

    with pytest.raises(TranslationEmptyInputError):
        asyncio.run(
            orchestrator.run(_build_config(input_dir=input_dir, output_dir=output_dir))
        )
