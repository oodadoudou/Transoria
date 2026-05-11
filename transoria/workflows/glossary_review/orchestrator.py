"""Glossary review orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.llm.client import LlmClient
from transoria.llm.config import effective_concurrency_limit
from transoria.runtime.cache import TaskCache
from transoria.runtime.executor import ProgressListener, SubtaskRunner, TaskExecutor
from transoria.runtime.key_pool import KeyPool
from transoria.runtime.rate_limit import TpmLimiter
from transoria.runtime.subtask import Subtask
from transoria.runtime.task_record import TaskRecord, TaskSnapshot
from transoria.workflows.glossary_review.config import GlossaryReviewConfig
from transoria.workflows.glossary_review.context import attach_reference_contexts
from transoria.workflows.glossary_review.exporters import (
    write_report,
    write_reviewed_xlsx,
)
from transoria.workflows.glossary_review.loader import (
    GlossaryReviewInputError,
    GlossaryReviewRow,
    LoadedGlossary,
    load_review_input,
)
from transoria.workflows.glossary_review.runner import (
    ReviewDecision,
    decode_review_response,
    encode_review_payload,
    GlossaryReviewSubtaskRunner,
)


CHARACTER_CATEGORY_KEYWORDS: frozenset[str] = frozenset(
    {
        "角色",
        "男性角色",
        "女性角色",
        "动物与非人角色",
        "历史与知名人物",
        "群体代称",
        "称呼与头衔",
        "ID与外号",
    }
)

_DEFAULT_RETRY_ATTEMPTS = 3
_DEFAULT_RETRY_INITIAL_BACKOFF_SECONDS = 1.0
_DEFAULT_RETRY_MAX_BACKOFF_SECONDS = 8.0


@dataclass(frozen=True)
class ReviewHistoryItem:
    dst: str
    info: str
    deleted: bool


@dataclass(frozen=True)
class GlossaryReviewResult:
    task_id: str
    output_path: Path | None
    report_path: Path | None
    changed_count: int
    final_status: TaskStatus


RunnerFactory = Callable[[LlmClient, GlossaryReviewConfig], SubtaskRunner]
ClockFn = Callable[[], str]
IdFactory = Callable[[], str]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_id_factory() -> str:
    return f"glossary-review-{uuid4().hex[:12]}"


def _default_runner_factory(
    client: LlmClient, config: GlossaryReviewConfig
) -> SubtaskRunner:
    tpm_limiter = (
        TpmLimiter(limit=config.model.tpm_limit)
        if config.model.tpm_limit > 0
        else None
    )
    key_pool = (
        KeyPool(config.model.api_keys)
        if config.model.rotate_keys and len(config.model.api_keys) > 1
        else None
    )
    return GlossaryReviewSubtaskRunner(
        client=client,
        model=replace(
            config.model,
            retry_attempts=max(config.model.retry_attempts, _DEFAULT_RETRY_ATTEMPTS),
            retry_initial_backoff_seconds=max(
                config.model.retry_initial_backoff_seconds,
                _DEFAULT_RETRY_INITIAL_BACKOFF_SECONDS,
            ),
            retry_max_backoff_seconds=max(
                config.model.retry_max_backoff_seconds,
                _DEFAULT_RETRY_MAX_BACKOFF_SECONDS,
            ),
        ),
        prompt_preset=config.prompt_preset,
        tpm_limiter=tpm_limiter,
        key_pool=key_pool,
        stream=config.stream,
        debug_log_dir=config.debug_log_dir,
    )


@dataclass
class GlossaryReviewOrchestrator:
    cache: TaskCache
    client: LlmClient
    runner_factory: RunnerFactory = field(default_factory=lambda: _default_runner_factory)
    progress: ProgressListener | None = None
    clock: ClockFn = _utc_now_iso
    id_factory: IdFactory = field(default_factory=lambda: _default_id_factory)
    on_executor_created: "Callable[[TaskExecutor], None] | None" = None
    on_result_finalized: "Callable[[GlossaryReviewResult], None] | None" = None

    async def run(self, config: GlossaryReviewConfig) -> GlossaryReviewResult:
        started_at = self.clock()
        task_id = self.id_factory()
        review_input = load_review_input(
            config.input_dir,
            output_filename=config.output_filename,
            selected_xlsx_path=config.selected_xlsx_path,
            selected_reference_paths=config.selected_reference_paths,
        )
        loaded = review_input.glossary
        rows = attach_reference_contexts(loaded.rows, review_input.reference_text)

        if not self.cache.has_task(task_id) or not self.cache.load_subtasks(task_id):
            self._seed_initial_task(
                task_id,
                started_at=started_at,
                config=config,
                loaded=loaded,
                reference_files=review_input.reference_files,
                rows=rows,
            )

        final_snapshot: TaskSnapshot | None = None
        for round_index in range(1, config.review_rounds + 1):
            rows, _report_rows = self._replay_completed(loaded.rows, self.cache.load(task_id))
            rows = attach_reference_contexts(rows, review_input.reference_text)
            history = self._review_history(loaded.rows, self.cache.load(task_id))
            self._ensure_round_seeded(
                task_id,
                round_index=round_index,
                config=config,
                rows=rows,
                history=history,
            )
            round_subtasks = [
                subtask
                for subtask in self.cache.load_subtasks(task_id)
                if _subtask_round(subtask) == round_index
            ]
            if not round_subtasks:
                self._save_round_progress(
                    task_id,
                    total_rounds=config.review_rounds,
                    current_round=round_index,
                    completed_rounds=round_index,
                    current_total_batches=0,
                    current_completed_batches=0,
                )
                continue
            if round_subtasks and all(
                subtask.status is SubtaskStatus.COMPLETED
                for subtask in round_subtasks
            ):
                self._save_round_progress(
                    task_id,
                    total_rounds=config.review_rounds,
                    current_round=round_index,
                    completed_rounds=round_index,
                    current_total_batches=len(round_subtasks),
                    current_completed_batches=len(round_subtasks),
                )
                continue
            self._reset_failed_round_subtasks(task_id, round_index=round_index)
            round_subtasks = [
                subtask
                for subtask in self.cache.load_subtasks(task_id)
                if _subtask_round(subtask) == round_index
            ]
            self._save_round_progress(
                task_id,
                total_rounds=config.review_rounds,
                current_round=round_index,
                completed_rounds=round_index - 1,
                current_total_batches=len(round_subtasks),
                current_completed_batches=_settled_round_subtasks(round_subtasks),
            )

            actual_concurrency = effective_concurrency_limit(config.model)
            config = replace(
                config,
                model=replace(config.model, concurrency_limit=actual_concurrency),
            )
            runner = self.runner_factory(self.client, config)
            executor = TaskExecutor(
                cache=self.cache,
                runner=runner,
                concurrency_limit=actual_concurrency,
                rpm_limit=max(0, config.model.rpm_limit),
                progress=self._round_progress_listener(
                    task_id=task_id,
                    total_rounds=config.review_rounds,
                    current_round=round_index,
                    current_total_batches=len(round_subtasks),
                ),
                clock=self.clock,
                stop_drain_seconds=max(5.0, float(config.model.timeout_seconds) + 5.0),
            )
            if self.on_executor_created is not None:
                self.on_executor_created(executor)
            final_snapshot = await executor.run(task_id)
            if _stopped_after_all_subtasks_completed(final_snapshot):
                self.cache.save_task(
                    final_snapshot.record.with_status(
                        TaskStatus.COMPLETED
                    ).with_updated_at(self.clock())
                )
                final_snapshot = self.cache.load(task_id)
            if final_snapshot.record.status is not TaskStatus.COMPLETED:
                return GlossaryReviewResult(
                    task_id=task_id,
                    output_path=None,
                    report_path=None,
                    changed_count=0,
                    final_status=final_snapshot.record.status,
                )
            if round_index < config.review_rounds:
                self.cache.save_task(
                    final_snapshot.record.with_status(
                        TaskStatus.RUNNING
                    ).with_updated_at(self.clock())
                )
                final_snapshot = self.cache.load(task_id)
            self._save_round_progress(
                task_id,
                total_rounds=config.review_rounds,
                current_round=round_index,
                completed_rounds=round_index,
                current_total_batches=len(round_subtasks),
                current_completed_batches=len(round_subtasks),
            )

        snapshot = self._complete_if_no_open_subtasks(task_id)
        rows, report_rows = self._replay_completed(
            attach_reference_contexts(loaded.rows, review_input.reference_text),
            snapshot,
        )
        output_path = write_reviewed_xlsx(
            loaded,
            rows,
            output_dir=config.input_dir,
            output_filename=config.output_filename,
        )
        report_payload = {
            "task_id": task_id,
            "generated_at": self.clock(),
            "input_xlsx": str(loaded.workbook_path),
            "output_path": str(output_path),
            "changed_count": len(report_rows),
            "rows": report_rows,
        }
        report_path = write_report(self.cache.task_dir(task_id), report_payload)
        result = GlossaryReviewResult(
            task_id=task_id,
            output_path=output_path,
            report_path=report_path,
            changed_count=len(report_rows),
            final_status=snapshot.record.status,
        )
        if self.on_result_finalized is not None:
            self.on_result_finalized(result)
        return result

    def _complete_if_no_open_subtasks(self, task_id: str) -> TaskSnapshot:
        snapshot = self.cache.load(task_id)
        progress = snapshot.progress()
        if (
            snapshot.record.status is not TaskStatus.COMPLETED
            and progress.pending == 0
            and progress.running == 0
            and progress.failed == 0
        ):
            self.cache.save_task(
                snapshot.record.with_status(TaskStatus.COMPLETED).with_updated_at(
                    self.clock()
                )
            )
            return self.cache.load(task_id)
        return snapshot

    def _save_round_progress(
        self,
        task_id: str,
        *,
        total_rounds: int,
        current_round: int,
        completed_rounds: int,
        current_total_batches: int,
        current_completed_batches: int,
    ) -> None:
        record = self.cache.load_record(task_id)
        metadata = dict(record.metadata)
        metadata["review_rounds_total"] = max(1, int(total_rounds))
        metadata["review_round_current"] = max(0, int(current_round))
        metadata["review_round_completed"] = max(0, int(completed_rounds))
        metadata["review_round_total_batches"] = max(0, int(current_total_batches))
        metadata["review_round_completed_batches"] = max(
            0, int(current_completed_batches)
        )
        self.cache.save_task(
            replace(record, metadata=metadata, updated_at=self.clock())
        )

    def _round_progress_listener(
        self,
        *,
        task_id: str,
        total_rounds: int,
        current_round: int,
        current_total_batches: int,
    ) -> ProgressListener:
        def _listener(event) -> None:
            round_subtasks = [
                subtask
                for subtask in event.snapshot.subtasks
                if _subtask_round(subtask) == current_round
            ]
            self._save_round_progress(
                task_id,
                total_rounds=total_rounds,
                current_round=current_round,
                completed_rounds=current_round - 1,
                current_total_batches=current_total_batches,
                current_completed_batches=_settled_round_subtasks(round_subtasks),
            )
            if self.progress is not None:
                self.progress(event)

        return _listener

    def _seed_initial_task(
        self,
        task_id: str,
        *,
        started_at: str,
        config: GlossaryReviewConfig,
        loaded: LoadedGlossary,
        reference_files: tuple[Path, ...],
        rows: tuple[GlossaryReviewRow, ...],
    ) -> None:
        record = TaskRecord(
            id=task_id,
            kind=TaskKind.GLOSSARY_REVIEW,
            status=TaskStatus.PENDING,
            created_at=started_at,
            updated_at=started_at,
            metadata={
                "input_dir": str(config.input_dir),
                "output_dir": str(config.input_dir),
                "output_filename": config.output_filename,
                "input_xlsx": str(loaded.workbook_path),
                "reference_files": [str(path) for path in reference_files],
                "review_rounds_total": config.review_rounds,
                "review_round_current": 0,
                "review_round_completed": 0,
                "review_round_total_batches": 0,
                "review_round_completed_batches": 0,
                "model_id": config.model.id,
                "prompt_preset_id": config.prompt_preset.id,
            },
        )
        subtasks = self._build_round_subtasks(
            task_id, round_index=1, config=config, rows=rows
        )
        self.cache.write_seed(record, subtasks)

    def _ensure_round_seeded(
        self,
        task_id: str,
        *,
        round_index: int,
        config: GlossaryReviewConfig,
        rows: tuple[GlossaryReviewRow, ...],
        history: Mapping[str, tuple[ReviewHistoryItem, ...]] | None = None,
    ) -> None:
        if any(
            _subtask_round(subtask) == round_index
            for subtask in self.cache.load_subtasks(task_id)
        ):
            return
        for subtask in self._build_round_subtasks(
            task_id, round_index=round_index, config=config, rows=rows, history=history
        ):
            self.cache.save_subtask(subtask)

    def _build_round_subtasks(
        self,
        task_id: str,
        *,
        round_index: int,
        config: GlossaryReviewConfig,
        rows: tuple[GlossaryReviewRow, ...],
        history: Mapping[str, tuple[ReviewHistoryItem, ...]] | None = None,
    ) -> list[Subtask]:
        active_rows = [
            row
            for row in rows
            if not row.deleted and not _has_consensus(row, round_index, history)
        ]
        batches = [
            active_rows[index : index + config.batch_size]
            for index in range(0, len(active_rows), config.batch_size)
        ]
        subtasks: list[Subtask] = []
        for batch_index, batch in enumerate(batches):
            payload_rows = tuple(
                _row_payload(row, config=config, history=history) for row in batch
            )
            subtasks.append(
                Subtask(
                    id=f"round-{round_index:02d}-batch-{batch_index:04d}",
                    task_id=task_id,
                    request_payload=encode_review_payload(
                        round_index=round_index,
                        batch_index=batch_index,
                        rows=payload_rows,
                        novel_background=config.novel_background,
                    ),
                )
            )
        return subtasks

    def _reset_failed_round_subtasks(self, task_id: str, *, round_index: int) -> None:
        for subtask in self.cache.load_subtasks(task_id):
            if _subtask_round(subtask) != round_index:
                continue
            if subtask.status is SubtaskStatus.FAILED:
                self.cache.save_subtask(
                    replace(
                        subtask,
                        status=SubtaskStatus.PENDING,
                        last_error="",
                        last_error_at="",
                    )
                )

    def _replay_completed(
        self, initial_rows: tuple[GlossaryReviewRow, ...], snapshot: TaskSnapshot
    ) -> tuple[tuple[GlossaryReviewRow, ...], list[dict[str, object]]]:
        rows = {row.row_index: row for row in initial_rows}
        report_rows: list[dict[str, object]] = []
        completed = sorted(
            (s for s in snapshot.subtasks if s.status is SubtaskStatus.COMPLETED),
            key=lambda s: (_subtask_round(s), int(s.request_payload.get("batch", 0))),
        )
        for subtask in completed:
            round_index = _subtask_round(subtask)
            for decision in decode_review_response(subtask.response_content):
                current = rows.get(decision.row_index)
                if current is None:
                    continue
                updated, report = _apply_decision(current, decision, round_index)
                rows[decision.row_index] = updated
                if report is not None:
                    report_rows.append(report)
        return tuple(rows[index] for index in sorted(rows)), report_rows

    def _review_history(
        self, initial_rows: tuple[GlossaryReviewRow, ...], snapshot: TaskSnapshot
    ) -> dict[str, tuple[ReviewHistoryItem, ...]]:
        rows = {row.row_index: row for row in initial_rows}
        history: dict[str, list[ReviewHistoryItem]] = {}
        completed = sorted(
            (s for s in snapshot.subtasks if s.status is SubtaskStatus.COMPLETED),
            key=lambda s: (_subtask_round(s), int(s.request_payload.get("batch", 0))),
        )
        for subtask in completed:
            for decision in decode_review_response(subtask.response_content):
                current = rows.get(decision.row_index)
                if current is None:
                    continue
                updated, _report = _apply_decision(
                    current, decision, _subtask_round(subtask)
                )
                history.setdefault(current.src, []).append(
                    ReviewHistoryItem(
                        dst=updated.dst,
                        info=updated.info,
                        deleted=updated.deleted,
                    )
                )
                rows[decision.row_index] = updated
        return {term: tuple(items) for term, items in history.items()}


def _row_payload(
    row: GlossaryReviewRow,
    *,
    config: GlossaryReviewConfig,
    history: Mapping[str, tuple[ReviewHistoryItem, ...]] | None,
) -> Mapping[str, object]:
    tier, instruction = _tier_instruction(row, config.novel_background)
    history_context = _history_context(row, history)
    return {
        "row_index": row.row_index,
        "src": row.src,
        "dst": row.dst,
        "info": row.info,
        "frequency": row.frequency,
        "tier": tier,
        "instruction": instruction,
        "history_context": history_context,
        "is_character": any(keyword in row.info for keyword in CHARACTER_CATEGORY_KEYWORDS),
        "current_category": row.info,
        "context": row.context,
    }


def _stopped_after_all_subtasks_completed(snapshot: TaskSnapshot) -> bool:
    progress = snapshot.progress()
    return (
        snapshot.record.status is TaskStatus.STOPPED
        and progress.total > 0
        and progress.pending == 0
        and progress.running == 0
        and progress.failed == 0
        and progress.completed == progress.total
    )


def _tier_instruction(row: GlossaryReviewRow, novel_background: str) -> tuple[str, str]:
    if row.src and row.src in novel_background:
        return "S", "【核心设定词】出现在背景设定中。必须严格保持一致，绝对禁止删除。"
    if row.frequency >= 5:
        return (
            "A",
            "【高频词】出现在原文多次。通常是重要术语，但若是被错误提取的通用常用词，请务必标记删除。",
        )
    if row.frequency <= 3:
        return (
            "C",
            "【低频词】仅出现1-3次。若判断为通用词汇、动词、形容词或无意义短语，请大胆建议删除。",
        )
    return "B", ""


def _history_context(
    row: GlossaryReviewRow,
    history: Mapping[str, tuple[ReviewHistoryItem, ...]] | None,
) -> str:
    if not history:
        return ""
    items = history.get(row.src)
    if not items:
        return ""
    latest = items[-1]
    if latest.deleted:
        return "之前已建议删除"
    parts = [f"之前已审定为: {latest.dst}"]
    if latest.info:
        parts.append(f"之前分类为: {latest.info}")
    return "；".join(parts)


def _has_consensus(
    row: GlossaryReviewRow,
    round_index: int,
    history: Mapping[str, tuple[ReviewHistoryItem, ...]] | None,
) -> bool:
    if round_index < 3 or not history:
        return False
    items = history.get(row.src, ())
    if len(items) < 2:
        return False
    latest = items[-1]
    previous = items[-2]
    return (
        latest.dst == previous.dst
        and latest.info == previous.info
        and latest.deleted == previous.deleted
    )


def _subtask_round(subtask: Subtask) -> int:
    try:
        return int(subtask.request_payload.get("round", 0))
    except (TypeError, ValueError):
        return 0


def _settled_round_subtasks(subtasks: tuple[Subtask, ...] | list[Subtask]) -> int:
    return sum(
        1
        for subtask in subtasks
        if subtask.status
        in (SubtaskStatus.COMPLETED, SubtaskStatus.FAILED, SubtaskStatus.SKIPPED)
    )


def _apply_decision(
    row: GlossaryReviewRow, decision: ReviewDecision, round_index: int
) -> tuple[GlossaryReviewRow, dict[str, object] | None]:
    if row.deleted:
        return row, None
    original_dst = row.dst
    original_info = row.info
    deleted = False
    next_dst = row.dst
    next_info = row.info

    if decision.action == "delete":
        deleted = True
    if decision.action in {"modify", "modify_category"} and decision.suggested_dst:
        next_dst = decision.suggested_dst
    if decision.action in {"category", "modify_category"} and decision.suggested_info:
        next_info = decision.suggested_info

    changed_dst = next_dst != original_dst
    changed_info = next_info != original_info
    if not deleted and not changed_dst and not changed_info:
        return row, None

    updated = GlossaryReviewRow(
        row_index=row.row_index,
        src=row.src,
        dst=next_dst,
        info=next_info,
        frequency=row.frequency,
        context=row.context,
        deleted=deleted,
    )
    if deleted:
        action = "delete"
    elif changed_dst and changed_info:
        action = "modify_category"
    elif changed_dst:
        action = "modify"
    else:
        action = "category"
    report = {
        "round": round_index,
        "action": action,
        "row_index": row.row_index,
        "src": row.src,
        "original_dst": original_dst,
        "suggested_dst": "" if deleted else next_dst,
        "original_info": original_info,
        "suggested_info": "" if deleted else next_info,
        "reason": decision.reason,
        "context_excerpt": row.context,
    }
    return updated, report


__all__ = [
    "GlossaryReviewInputError",
    "GlossaryReviewOrchestrator",
    "GlossaryReviewResult",
]
