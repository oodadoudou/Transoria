"""Phase 1.9 focused tests.

Each test in this file asserts exactly one contract clause that the prior
suite did not cover. The targets came from a recursive review of the
post-Phase-1.8 codebase.

Per ``docs/test-strategy.md``: minimal surface, one subject per test, no
parametrised noise.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

from transoria.domain import Language, SubtaskStatus, TaskKind, TaskStatus
from transoria.llm import LlmClient, ModelConfig, ProviderFormat
from transoria.llm.client import TransportResult
from transoria.prompts import (
    PromptKind,
    PromptPreset,
    PromptPresetStore,
    default_preset,
)
from transoria.runtime import (
    Subtask,
    SubtaskResult,
    SubtaskRunner,
    TaskCache,
    TaskExecutor,
    TaskNotFoundError,
    TaskRecord,
)
from transoria.runtime.rate_limit import TpmLimiter
from transoria.workflows.glossary import (
    GlossaryConfig,
    GlossaryOrchestrator,
    GlossaryRecord,
)
from transoria.workflows.novel_mode import (
    NovelModeConfig,
    NovelModeOrchestrator,
)
from transoria.workflows.translation import (
    Glossary,
    TranslationConfig,
)


# ---------------------------------------------------------------------------
# Contract: a failed subtask carries a structured error code prefix in
# last_error so the frontend can localise without parsing exception text.
# ---------------------------------------------------------------------------


def test_subtask_last_error_carries_structured_error_code(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)
    cache.write_seed(
        TaskRecord(id="t", kind=TaskKind.TRANSLATION),
        [Subtask(id="s0", task_id="t")],
    )

    class FailingRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            from transoria.llm import LlmRequestError

            raise LlmRequestError("boom", code="llm.line_count_mismatch")

    asyncio.run(
        TaskExecutor(cache=cache, runner=FailingRunner(), rpm_limit=0).run("t")
    )

    failed = cache.load_subtasks("t")[0]
    assert failed.status is SubtaskStatus.FAILED
    assert failed.last_error.startswith("[llm.line_count_mismatch]")


# ---------------------------------------------------------------------------
# Contract: PromptPresetStore.save is atomic. A failed write does not leave
# the store in a corrupt or partially-written state.
# ---------------------------------------------------------------------------


def test_prompt_preset_store_failed_write_does_not_corrupt_existing_file(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )
    # Seed a known good file.
    store.save([default_preset(PromptKind.TRANSLATION)])
    good_bytes = store.path.read_bytes()

    # Force the rename step to fail by patching os.replace; the original
    # file must remain intact afterwards.
    import os

    real_replace = os.replace

    def boom(*args: object, **kwargs: object) -> None:  # noqa: ANN401
        raise OSError("simulated crash during rename")

    os.replace = boom  # type: ignore[assignment]
    try:
        with pytest.raises(OSError):
            store.save(
                [
                    default_preset(PromptKind.TRANSLATION),
                    PromptPreset(
                        id="x",
                        name="x",
                        kind=PromptKind.TRANSLATION,
                        system_prompt="hi {target_language}",
                    ),
                ]
            )
    finally:
        os.replace = real_replace  # type: ignore[assignment]

    # Original file must be unchanged.
    assert store.path.read_bytes() == good_bytes


# ---------------------------------------------------------------------------
# Contract: TaskCache.write_seed commits subtasks before the task header so
# a crash mid-loop leaves no orphan record.
# ---------------------------------------------------------------------------


def test_task_cache_crashes_during_seed_leave_no_loadable_record(tmp_path: Path) -> None:
    cache = TaskCache(root=tmp_path)

    record = TaskRecord(id="t-crash", kind=TaskKind.TRANSLATION)

    class _ExplodingIterable:
        def __iter__(self):
            yield Subtask(id="s0", task_id="t-crash")
            raise RuntimeError("simulated crash mid-write_seed")

    with pytest.raises(RuntimeError, match="simulated crash"):
        cache.write_seed(record, _ExplodingIterable())

    # The first subtask file may exist on disk, but no task.json was
    # written, so load_record correctly reports the task as missing.
    with pytest.raises(TaskNotFoundError):
        cache.load_record("t-crash")


# ---------------------------------------------------------------------------
# Contract: Novel-mode aborts stage 2 when stage 1 fails and
# abort_on_glossary_failure is True (default).
# ---------------------------------------------------------------------------


@dataclass
class _GlossaryAlwaysFailsTransport:
    """Always returns a 500 — every glossary chunk fails."""

    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests.append(dict(payload))
        return TransportResult(500, {"error": "permanent failure"})


def test_novel_mode_skips_translation_when_stage_one_fails(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Sample.txt").write_text("신해범 walks.\n", encoding="utf-8")

    transport = _GlossaryAlwaysFailsTransport()
    orchestrator = NovelModeOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
    )

    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://x/",
        model_id="m",
        api_keys=("k",),
        retry_attempts=0,
        rpm_limit=0,
    )
    config = NovelModeConfig(
        glossary=GlossaryConfig(
            input_dir=input_dir,
            output_dir=output_dir,
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
            model=model,
            prompt_preset=default_preset(PromptKind.GLOSSARY),
            chunk_char_limit=200,
        ),
        translation=TranslationConfig(
            input_dir=input_dir,
            output_dir=output_dir,
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
            model=model,
            prompt_preset=default_preset(PromptKind.TRANSLATION),
            glossary=Glossary.empty(),
        ),
    )

    result = asyncio.run(orchestrator.run(config))

    assert result.glossary.final_status is TaskStatus.FAILED
    # Stage 2 must not have run when stage 1 failed and abort flag is on.
    assert result.translation is None
    assert result.used_extracted_glossary is False


# ---------------------------------------------------------------------------
# Contract: Combined folder-level glossary materialises a ``<folder>``-named
# artifact set in addition to the per-file artifacts.
# ---------------------------------------------------------------------------


@dataclass
class _CandidateTransport:
    candidates: tuple[tuple[str, str, str], ...] = (
        ("신해범", "申海范", "Male Name"),
    )

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        lines = [
            json.dumps({"src": s, "dst": d, "type": i}, ensure_ascii=False)
            for s, d, i in self.candidates
        ]
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return TransportResult(200, body)


def test_combined_folder_glossary_emits_folder_named_artifact_set(tmp_path: Path) -> None:
    input_dir = tmp_path / "Series-Vol"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Vol1.txt").write_text("신해범 walks.\n", encoding="utf-8")
    (input_dir / "Vol2.txt").write_text("신해범 sits.\n", encoding="utf-8")

    transport = _CandidateTransport()
    orchestrator = GlossaryOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-combined",
    )
    model = ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://x/",
        model_id="m",
        api_keys=("k",),
        retry_attempts=0,
        rpm_limit=0,
    )

    asyncio.run(
        orchestrator.run(
            GlossaryConfig(
                input_dir=input_dir,
                output_dir=output_dir,
                source_language=Language.KOREAN,
                target_language=Language.CHINESE_SIMPLIFIED,
                model=model,
                prompt_preset=default_preset(PromptKind.GLOSSARY),
                chunk_char_limit=200,
                combine_folder_glossary=True,
            )
        )
    )

    folder_xlsx = output_dir / f"{input_dir.resolve().name}-Glossary.xlsx"
    folder_json = output_dir / f"{input_dir.resolve().name}-Glossary.json"
    folder_refs = output_dir / f"{input_dir.resolve().name}-Glossary-references.txt"
    assert folder_xlsx.exists()
    assert folder_json.exists()
    assert folder_refs.exists()


# ---------------------------------------------------------------------------
# Contract: TpmLimiter.reserve propagates asyncio.CancelledError without
# leaking partial state.
# ---------------------------------------------------------------------------


def test_tpm_limiter_reserve_propagates_cancellation() -> None:
    sleep_called = asyncio.Event()

    async def fake_sleep(_seconds: float) -> None:
        sleep_called.set()
        # Block until cancelled.
        await asyncio.Future()

    limiter = TpmLimiter(limit=10, window=60.0, sleep=fake_sleep)

    async def scenario() -> None:
        # Saturate the budget.
        await limiter.reserve(10)
        # The next reserve() will sleep waiting for budget; cancel it.
        task = asyncio.create_task(limiter.reserve(5))
        await sleep_called.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    # First reservation is still recorded; cancelled one is not.
    assert limiter.used_in_window() == 10
