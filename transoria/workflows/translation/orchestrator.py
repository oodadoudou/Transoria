"""Translation orchestrator: scan → parse → translate → write → report.

The orchestrator is the only place that knows about all of: the input/output
filesystem layout, the per-format parsers and writers, the LLM client, and
the runtime executor. It builds the per-file segment lists, preprocesses
each segment, splits them into chunks, hands a single task to the executor,
and after the task settles writes per-format outputs and the run statistics
file.

Design decisions worth calling out:

- One :class:`TaskExecutor` invocation handles the whole run, even when
  multiple files are present. Subtasks carry a stable ``segment_id`` of the
  form ``"<file_index>:<segment_index>"`` so the orchestrator can route each
  decoded result back to the right file at writeback time.
- After the executor + split-failed-chunks loop finishes, if any FAILED
  subtasks remain the orchestrator runs up to ``_AUTO_RETRY_MAX_ROUNDS``
  (3) extra recovery rounds. Each round waits
  ``_AUTO_RETRY_DELAY_SECONDS`` (60s — long enough for a typical RPM
  rate-limit window to reset), resets the FAILED subtasks back to
  PENDING, and re-runs the executor. The loop exits early once no
  FAILED subtasks remain. This catches the common rate-limit /
  transient-network case without forcing the user to manually click
  "Continue".
- User-facing translated files are written on any terminal status
  (COMPLETED or FAILED) that produced at least one translated segment.
  Missing segments fall back to original source text in the writers,
  so a forever-broken API does not block the user from getting at
  least the partial deliverable. ``_maybe_cleanup_cache`` still keeps
  the cache when failures > 0 so the user can manually
  ``continue_task`` to rerun the still-failed chunks beyond the
  built-in auto-retry.
- Bilingual output goes under a single shared subfolder (per design doc),
  not a per-file subfolder. The English and Chinese UI defaults are exposed
  as ``BILINGUAL_OUTPUT_FOLDER_EN`` / ``..._ZH`` constants.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.formats.epub_parser import parse_epub_file
from transoria.formats.epub_writer import write_bilingual_epub, write_translated_epub
from transoria.formats.scanner import scan_input_directory
from transoria.formats.text import (
    parse_txt_file,
    write_bilingual_txt,
    write_translated_txt,
)
from transoria.llm.client import LlmClient
from transoria.llm.config import effective_concurrency_limit
from transoria.runtime.cache import TaskCache
from transoria.runtime.executor import (
    ProgressListener,
    SubtaskRunner,
    TaskExecutor,
)
from transoria.runtime.key_pool import KeyPool
from transoria.runtime.rate_limit import TpmLimiter
from transoria.runtime.subtask import Subtask
from transoria.runtime.task_record import TaskRecord
from transoria.workflows.translation.chunker import (
    PreparedSegment,
    build_chunks,
)
from transoria.workflows.translation.config import TranslationConfig
from transoria.workflows.prefilter import should_translate_for_language
from transoria.workflows.translation.preprocessor import (
    preprocess_segment,
    strip_drm_invisibles,
)
from transoria.workflows.translation.runner import (
    TranslationSubtaskRunner,
    encode_subtask_payload,
)
from transoria.workflows.translation.statistics import (
    FailedFile,
    LowConfidenceSegment,
    TranslationStatistics,
    write_translation_statistics,
)


# Wait between automatic recovery rounds. Bounded by upstream API
# rate-limit windows (RPM is the common case), not user preference,
# so it stays a constant. The number of rounds itself is the
# user-facing knob (``TranslationConfig.auto_retry_max_rounds``).
_AUTO_RETRY_DELAY_SECONDS = 30.0

# 32-line chunk that keeps failing → round 0: 16+16 → round 1: 8+8+8+8 → stop.
# Smaller than 8 starts paying singleton prompt-overhead without obvious
# reliability gains, so the cap is 2.
_SPLIT_ROUNDS = 2


class TranslationEmptyInputError(RuntimeError):
    """Raised when translation would produce no output because the
    input has no usable text. ``_on_task_failure`` records the message
    in ``record.metadata.last_error`` so the Run page surfaces a
    specific "why" — far more useful than a silent COMPLETED / 0/0."""


@dataclass(frozen=True)
class TranslationRunResult:
    task_id: str
    statistics: TranslationStatistics
    statistics_path: Path
    translated_outputs: tuple[Path, ...]
    bilingual_outputs: tuple[Path, ...]
    final_status: TaskStatus


@dataclass(frozen=True)
class _ParsedFile:
    file_index: int
    document_kind: str  # "txt" or "epub"
    document: object  # TextDocument | EpubDocument
    source_segments: tuple[tuple[int, str], ...]  # (segment_index, source_text)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TranslationOrchestrator:
    """Top-level entry point for a translation run.

    Construct once with infrastructure dependencies (cache root, LLM client),
    then call :meth:`run` with a :class:`TranslationConfig` per run.
    """

    cache: TaskCache
    client: LlmClient
    runner_factory: "RunnerFactory" = field(default_factory=lambda: _default_runner_factory)
    progress: ProgressListener | None = None
    clock: "ClockFn" = _utc_now_iso
    id_factory: "IdFactory" = field(default_factory=lambda: _default_id_factory)
    on_executor_created: "Callable[[TaskExecutor], None] | None" = None
    on_result_finalized: "Callable[[TranslationRunResult], None] | None" = None

    async def run(self, config: TranslationConfig) -> TranslationRunResult:
        started_at = self.clock()
        parsed_files = _scan_and_parse(
            config.input_dir, buffer_epub_archives=config.buffer_epub_archives
        )
        prepared, prepared_per_file = _prepare_segments(parsed_files, config)

        if not prepared:
            return self._finalize_empty(
                config,
                started_at,
                reason=(
                    "Input folder contained supported files but none yielded any "
                    "translatable text (files may be empty, corrupt, or only "
                    "contain metadata)."
                ),
            )

        chunks = build_chunks(
            prepared,
            chunk_size=config.chunk_size,
            chunk_token_limit=config.chunk_token_limit,
            token_counter=config.token_counter,
            context_line_count=config.context_line_count,
            glossary=config.glossary,
        )

        task_id = self.id_factory()
        # "Continue path" means subtasks were already seeded — i.e. the
        # orchestrator ran for this task_id at least once. A bare
        # TaskRecord without subtasks (e.g. a placeholder seeded by
        # TaskService for early read_snapshot reads) is treated as
        # "fresh start".
        existing_subtasks = (
            self.cache.load_subtasks(task_id) if self.cache.has_task(task_id) else ()
        )
        if existing_subtasks:
            # Continue path: reuse the prior cache record + subtasks.
            # Reset FAILED subtasks to PENDING so the executor reruns
            # them; COMPLETED subtasks stay completed and are skipped.
            # Re-chunking is intentionally avoided so subtask ids stay
            # stable across stop → continue cycles.
            for stored in existing_subtasks:
                if stored.status is SubtaskStatus.FAILED:
                    self.cache.save_subtask(
                        replace(
                            stored,
                            status=SubtaskStatus.PENDING,
                            last_error="",
                            last_error_at="",
                        )
                    )
        else:
            record = TaskRecord(
                id=task_id,
                kind=TaskKind.TRANSLATION,
                status=TaskStatus.PENDING,
                created_at=started_at,
                updated_at=started_at,
                metadata={
                    "input_dir": str(config.input_dir),
                    "output_dir": str(config.output_dir),
                    "source_language": config.source_language.value,
                    "target_language": config.target_language.value,
                    "model_id": config.model.id,
                    "prompt_preset_id": config.prompt_preset.id,
                    # Snapshot for proofreading retranslate — must use the
                    # same prompt/glossary/rules the original run used.
                    "prompt_preset": config.prompt_preset.to_dict(),
                    "glossary": [
                        {
                            "src": entry.src,
                            "dst": entry.dst,
                            "info": entry.info,
                            "regex": entry.regex,
                            "case_sensitive": entry.case_sensitive,
                            "enabled": entry.enabled,
                        }
                        for entry in config.glossary.entries
                    ],
                    "text_preserve_rules": [
                        {"pattern": r.pattern, "note": r.note, "enabled": r.enabled}
                        for r in config.text_preserve_rules
                    ],
                    "pre_replacements": [
                        {
                            "src": r.src,
                            "dst": r.dst,
                            "regex": r.regex,
                            "case_sensitive": r.case_sensitive,
                            "note": r.note,
                            "enabled": r.enabled,
                        }
                        for r in config.pre_replacements
                    ],
                    "post_replacements": [
                        {
                            "src": r.src,
                            "dst": r.dst,
                            "regex": r.regex,
                            "case_sensitive": r.case_sensitive,
                            "note": r.note,
                            "enabled": r.enabled,
                        }
                        for r in config.post_replacements
                    ],
                },
            )

            subtasks: list[Subtask] = []
            per_file_lookup = {item.segment_id: item for item in prepared}
            for chunk_index, chunk in enumerate(chunks):
                metadata = [
                    _segment_metadata(per_file_lookup[segment.segment_id])
                    for segment in chunk.segments
                ]
                payload = encode_subtask_payload(chunk, segment_metadata=metadata)
                subtasks.append(
                    Subtask(
                        id=f"chunk-{chunk_index:05d}",
                        task_id=task_id,
                        request_payload=payload,
                    )
                )

            self.cache.write_seed(record, subtasks)

        actual_concurrency = effective_concurrency_limit(config.model)
        config = replace(
            config, model=replace(config.model, concurrency_limit=actual_concurrency)
        )
        runner = self.runner_factory(self.client, config)
        executor = TaskExecutor(
            cache=self.cache,
            runner=runner,
            concurrency_limit=actual_concurrency,
            rpm_limit=max(0, config.model.rpm_limit),
            progress=self.progress,
            clock=self.clock,
            # Drain in-flight LLM calls naturally on stop instead of
            # cancelling mid-call. Bound by the model's per-request
            # timeout (with headroom) so a wedged HTTP call still
            # eventually unsticks Stop.
            stop_drain_seconds=max(5.0, float(config.model.timeout_seconds) + 5.0),
        )
        if self.on_executor_created is not None:
            self.on_executor_created(executor)

        snapshot = await executor.run(task_id)
        # All subsequent recovery loops must respect the stop signal —
        # otherwise pressing Stop and waiting for in-flight requests to
        # drain would just be followed by either a split-rerun or a
        # 30-second sleep + auto-retry, defeating the user's intent.
        while (
            not executor.is_stopping
            and self._split_failed_subtasks(task_id, snapshot.subtasks, config)
        ):
            snapshot = await executor.run(task_id)

        # Auto-retry loop: rate-limit and other transient failures often
        # clear in 30+ seconds. Reset still-FAILED subtasks back to
        # PENDING and re-run, up to ``auto_retry_max_rounds`` times,
        # waiting between rounds so the upstream limiter actually has a
        # chance to reset. Stops early when no failures remain or the
        # user pressed Stop (the sleep itself polls the stop flag every
        # second so we abort the wait promptly).
        for _ in range(max(0, config.auto_retry_max_rounds)):
            if executor.is_stopping:
                break
            if not _has_failed_subtasks(snapshot.subtasks):
                break
            # Flip RUNNING *before* the sleep so the 30s wait window
            # does not show a stale FAILED status to pollers (which
            # would stop them and hide the eventual COMPLETED). On
            # stop during the sleep we restore STOPPED so user intent
            # is honored — no executor.run will fire after this break.
            self._mark_task_running(task_id)
            if not await _interruptible_sleep(
                _AUTO_RETRY_DELAY_SECONDS, executor
            ):
                self._mark_task_status(task_id, TaskStatus.STOPPED)
                break
            self._reset_failed_subtasks(snapshot.subtasks)
            snapshot = await executor.run(task_id)

        translations_by_segment, low_confidence_records = _collect_translations(
            snapshot.subtasks
        )
        snapshot_progress = snapshot.progress()
        all_segments_translated = len(translations_by_segment) == len(prepared)
        if (
            snapshot.record.status is TaskStatus.STOPPED
            and all_segments_translated
            and snapshot_progress.pending == 0
            and snapshot_progress.running == 0
            and snapshot_progress.failed == 0
        ):
            self._mark_task_status(task_id, TaskStatus.COMPLETED)
            snapshot = self.cache.load(task_id)
        # Outputs are written on any terminal status that produced at
        # least one translated segment. Missing segments fall back to
        # original source text in the writers, so a forever-broken API
        # still yields the partial deliverable. Cache cleanup
        # (``_maybe_cleanup_cache``) only fires on clean COMPLETED
        # with zero failures, so the user can still manually
        # ``continue_task`` to fill in the remaining gaps after the
        # built-in auto-retry rounds run out.
        terminal_status = snapshot.record.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        )
        if terminal_status and translations_by_segment:
            translated_outputs, bilingual_outputs, failed_files = _write_outputs(
                parsed_files,
                translations_by_segment,
                prepared_per_file,
                config,
            )
        else:
            translated_outputs = []
            bilingual_outputs = []
            failed_files = _failed_files_for_missing_translations(
                parsed_files, translations_by_segment, prepared_per_file
            )

        statistics = TranslationStatistics(
            started_at=started_at,
            ended_at=self.clock(),
            processed_files=tuple(
                str(parsed.document.path)  # type: ignore[union-attr]
                for parsed in parsed_files
            ),
            translated_outputs=tuple(str(path) for path in translated_outputs),
            bilingual_outputs=tuple(str(path) for path in bilingual_outputs),
            total_segments=len(prepared),
            completed_segments=len(translations_by_segment),
            failed_subtasks=sum(
                1 for s in snapshot.subtasks if s.status is SubtaskStatus.FAILED
            ),
            failed_files=tuple(failed_files),
            low_confidence_segments=tuple(
                LowConfidenceSegment(
                    segment_id=str(record["segment_id"]),
                    reasons=tuple(record.get("reasons", [])),  # type: ignore[arg-type]
                )
                for record in low_confidence_records
            ),
            usage=snapshot.usage(),
        )
        failed_subtask_details = tuple(
            (s.id, s.last_error)
            for s in snapshot.subtasks
            if s.status is SubtaskStatus.FAILED and s.last_error
        )
        statistics_path = write_translation_statistics(
            statistics,
            self.cache.task_dir(task_id),
            failed_subtask_details=failed_subtask_details,
        )

        result = TranslationRunResult(
            task_id=task_id,
            statistics=statistics,
            statistics_path=statistics_path,
            translated_outputs=tuple(translated_outputs),
            bilingual_outputs=tuple(bilingual_outputs),
            final_status=snapshot.record.status,
        )
        if self.on_result_finalized is not None:
            self.on_result_finalized(result)
        return result

    def _finalize_empty(
        self,
        config: TranslationConfig,
        started_at: str,
        *,
        reason: str = "",
    ) -> TranslationRunResult:
        # Empty input is treated as FAILED with a typed reason rather
        # than silently COMPLETED — see the matching note in
        # ``transoria.workflows.glossary.orchestrator``.
        if reason:
            raise TranslationEmptyInputError(reason)
        statistics = TranslationStatistics(
            started_at=started_at,
            ended_at=self.clock(),
        )
        statistics_path = write_translation_statistics(
            statistics, config.output_dir
        )
        result = TranslationRunResult(
            task_id="",
            statistics=statistics,
            statistics_path=statistics_path,
            translated_outputs=(),
            bilingual_outputs=(),
            final_status=TaskStatus.COMPLETED,
        )
        if self.on_result_finalized is not None:
            self.on_result_finalized(result)
        return result

    def _mark_task_status(self, task_id: str, status: TaskStatus) -> None:
        record = self.cache.load_record(task_id)
        if record.status is status:
            return
        self.cache.save_task(
            record.with_status(status).with_updated_at(self.clock())
        )

    def _mark_task_running(self, task_id: str) -> None:
        # Between recovery rounds the executor's _finalize writes
        # FAILED to disk. Frontend polling treats FAILED as terminal
        # and stops, so the user never sees the eventual COMPLETED.
        # Flip the record back to RUNNING right before we commit more
        # work, so the transient FAILED does not leak to pollers.
        self._mark_task_status(task_id, TaskStatus.RUNNING)

    def _reset_failed_subtasks(self, subtasks: tuple[Subtask, ...]) -> int:
        """Flip every FAILED subtask back to PENDING for the auto-retry
        loop. Mirrors the continue_task reset path so a clean re-run
        can replace the prior failure record. Returns the count of
        subtasks reset (callers can short-circuit when 0)."""

        reset = 0
        for subtask in subtasks:
            if subtask.status is not SubtaskStatus.FAILED:
                continue
            self.cache.save_subtask(
                replace(
                    subtask,
                    status=SubtaskStatus.PENDING,
                    last_error="",
                    last_error_at="",
                )
            )
            reset += 1
        return reset

    def _split_failed_subtasks(
        self,
        task_id: str,
        subtasks: tuple[Subtask, ...],
        config: TranslationConfig,
    ) -> int:
        if _SPLIT_ROUNDS <= 0:
            return 0
        created = 0
        for subtask in subtasks:
            if subtask.status is not SubtaskStatus.FAILED:
                continue
            child_payloads = _split_failed_payload(
                subtask.request_payload,
                parent_subtask_id=subtask.id,
                max_rounds=_SPLIT_ROUNDS,
            )
            if not child_payloads:
                continue
            if created == 0:
                self._mark_task_running(task_id)
            self.cache.save_subtask(
                replace(
                    subtask,
                    status=SubtaskStatus.SKIPPED,
                    last_error=f"split into {len(child_payloads)} child subtasks after: {subtask.last_error}",
                )
            )
            for index, payload in enumerate(child_payloads):
                child_id = f"{subtask.id}.s{payload['split_round']}.{index}"
                self.cache.save_subtask(
                    Subtask(
                        id=child_id,
                        task_id=task_id,
                        request_payload=payload,
                    )
                )
                created += 1
        return created


def _scan_and_parse(input_dir: Path, *, buffer_epub_archives: bool) -> tuple[_ParsedFile, ...]:
    discovered = scan_input_directory(input_dir)
    parsed: list[_ParsedFile] = []
    for index, document_file in enumerate(discovered):
        if document_file.format.value == "txt":
            text_doc = parse_txt_file(document_file.path)
            parsed.append(
                _ParsedFile(
                    file_index=index,
                    document_kind="txt",
                    document=text_doc,
                    source_segments=tuple(
                        (segment.index, segment.text)
                        for segment in text_doc.segments
                    ),
                )
            )
        else:
            epub_doc = parse_epub_file(
                document_file.path, buffer_archive=buffer_epub_archives
            )
            parsed.append(
                _ParsedFile(
                    file_index=index,
                    document_kind="epub",
                    document=epub_doc,
                    source_segments=tuple(
                        (segment.index, segment.text)
                        for segment in epub_doc.segments
                    ),
                )
            )
    return tuple(parsed)


def _prepare_segments(
    parsed_files: tuple[_ParsedFile, ...], config: TranslationConfig
) -> tuple[tuple[PreparedSegment, ...], dict[int, tuple[PreparedSegment, ...]]]:
    """Run the preprocessor over every segment of every file.

    Empty/whitespace-only segments are dropped so they never reach the LLM,
    and the writer just keeps the original line.
    """

    flat: list[PreparedSegment] = []
    per_file: dict[int, list[PreparedSegment]] = {}
    for parsed in parsed_files:
        bucket: list[PreparedSegment] = []
        for segment_index, raw_source_text in parsed.source_segments:
            # Strip DRM invisible chars so confidence checks and bilingual
            # output see the same clean text the LLM does.
            source_text = strip_drm_invisibles(raw_source_text)
            if not should_translate_for_language(
                source_text,
                source_language=config.source_language,
                target_language=config.target_language,
            ):
                continue
            preprocessed = preprocess_segment(
                source_text,
                text_preserve_rules=config.text_preserve_rules,
                pre_replacements=config.pre_replacements,
            )
            if not should_translate_for_language(
                preprocessed.prompt_text,
                source_language=config.source_language,
                target_language=config.target_language,
            ):
                continue
            prepared = PreparedSegment(
                segment_id=f"{parsed.file_index}:{segment_index}",
                original_text=source_text,
                preprocessed=preprocessed,
            )
            bucket.append(prepared)
            flat.append(prepared)
        per_file[parsed.file_index] = tuple(bucket)
    return tuple(flat), {idx: tuple(items) for idx, items in per_file.items()}


def _segment_metadata(prepared: PreparedSegment) -> Mapping[str, object]:
    return {
        "original_text": prepared.original_text,
        "protection_spans": list(prepared.preprocessed.protection.spans),
        "leading_whitespace": prepared.preprocessed.leading_whitespace,
        "trailing_whitespace": prepared.preprocessed.trailing_whitespace,
    }


def _split_failed_payload(
    payload: Mapping[str, object],
    *,
    parent_subtask_id: str,
    max_rounds: int,
) -> tuple[dict[str, object], ...]:
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or len(raw_segments) <= 1:
        return ()
    split_round = int(payload.get("split_round", 0))
    if split_round >= max_rounds:
        return ()
    midpoint = max(1, len(raw_segments) // 2)
    groups = (raw_segments[:midpoint], raw_segments[midpoint:])
    children: list[dict[str, object]] = []
    for split_index, group in enumerate(groups):
        if not group:
            continue
        child_segments: list[dict[str, object]] = []
        for chunk_index, raw_segment in enumerate(group):
            if not isinstance(raw_segment, Mapping):
                continue
            child = dict(raw_segment)
            child["chunk_index"] = chunk_index
            child_segments.append(child)
        if not child_segments:
            continue
        child_payload = dict(payload)
        child_payload["segments"] = child_segments
        child_payload["context_lines"] = []
        child_payload["parent_subtask_id"] = parent_subtask_id
        child_payload["split_round"] = split_round + 1
        child_payload["split_index"] = split_index
        children.append(child_payload)
    return tuple(children)


def _collect_translations(
    subtasks: tuple[Subtask, ...],
) -> tuple[dict[str, str], list[dict[str, object]]]:
    """Collect translations and low-confidence reports from completed subtasks.

    Supports both response shapes:
    - v2 (current): ``{"version": 2, "translations": {...}, "low_confidence": [...]}``.
    - v1 (legacy): the flat ``{segment_id: text}`` map.

    Returns ``(translations, low_confidence_records)`` where each
    low-confidence record is ``{"segment_id": str, "reasons": list[str]}``.
    """

    translations: dict[str, str] = {}
    low_confidence: list[dict[str, object]] = []
    for subtask in subtasks:
        if subtask.status is not SubtaskStatus.COMPLETED:
            continue
        if not subtask.response_content:
            continue
        try:
            payload = json.loads(subtask.response_content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        if "translations" in payload and isinstance(
            payload.get("translations"), Mapping
        ):
            for segment_id, text in payload["translations"].items():
                translations[str(segment_id)] = str(text)
            raw_low = payload.get("low_confidence", [])
            if isinstance(raw_low, list):
                for record in raw_low:
                    if not isinstance(record, Mapping):
                        continue
                    reasons = record.get("reasons", [])
                    if not isinstance(reasons, list):
                        reasons = []
                    low_confidence.append(
                        {
                            "segment_id": str(record.get("segment_id", "")),
                            "reasons": [str(reason) for reason in reasons],
                        }
                    )
        else:
            for segment_id, text in payload.items():
                translations[str(segment_id)] = str(text)
    return translations, low_confidence


def _has_failed_subtasks(subtasks: tuple[Subtask, ...]) -> bool:
    return any(s.status is SubtaskStatus.FAILED for s in subtasks)


async def _interruptible_sleep(seconds: float, executor: TaskExecutor) -> bool:
    """Sleep up to ``seconds`` while polling the executor's stop flag
    every second. Returns True if the wait completed naturally,
    False if the wait was cut short by a stop request — callers should
    skip whatever was queued after the wait when the result is False.
    """

    if seconds <= 0:
        return not executor.is_stopping
    elapsed = 0.0
    while elapsed < seconds:
        if executor.is_stopping:
            return False
        step = min(1.0, seconds - elapsed)
        await asyncio.sleep(step)
        elapsed += step
    return not executor.is_stopping


def _failed_files_for_missing_translations(
    parsed_files: tuple[_ParsedFile, ...],
    translations_by_segment: Mapping[str, str],
    prepared_per_file: Mapping[int, tuple[PreparedSegment, ...]],
) -> list[FailedFile]:
    failed_files: list[FailedFile] = []
    for parsed in parsed_files:
        missing_for_file = sum(
            1
            for item in prepared_per_file.get(parsed.file_index, ())
            if item.segment_id not in translations_by_segment
        )
        if missing_for_file:
            failed_files.append(
                FailedFile(
                    path=str(parsed.document.path),  # type: ignore[union-attr]
                    reason=f"{missing_for_file} segments missing from translation results",
                    code="missing_translations",
                    details={"missing_segments": missing_for_file},
                )
            )
    return failed_files


def _write_outputs(
    parsed_files: tuple[_ParsedFile, ...],
    translations_by_segment: Mapping[str, str],
    prepared_per_file: Mapping[int, tuple[PreparedSegment, ...]],
    config: TranslationConfig,
) -> tuple[list[Path], list[Path], list[FailedFile]]:
    translated_outputs: list[Path] = []
    bilingual_outputs: list[Path] = []
    failed_files: list[FailedFile] = []

    for parsed in parsed_files:
        prepared = prepared_per_file.get(parsed.file_index, ())
        per_file_translations: dict[int, str] = {}
        missing_for_file = 0
        for item in prepared:
            translation = translations_by_segment.get(item.segment_id)
            if translation is None:
                missing_for_file += 1
                continue
            segment_index = int(item.segment_id.split(":", 1)[1])
            per_file_translations[segment_index] = translation

        if not per_file_translations:
            failed_files.append(
                FailedFile(
                    path=str(parsed.document.path),  # type: ignore[union-attr]
                    reason="no translated segments matched this file",
                    code="no_matching_translations",
                    details={"expected_segments": len(prepared)},
                )
            )
            continue

        try:
            if parsed.document_kind == "txt":
                translated_path = write_translated_txt(
                    parsed.document,  # type: ignore[arg-type]
                    per_file_translations,
                    config.output_dir,
                    target_language=config.target_language,
                )
                translated_outputs.append(translated_path)
                if config.bilingual_enabled:
                    bilingual_path = write_bilingual_txt(
                        parsed.document,  # type: ignore[arg-type]
                        per_file_translations,
                        config.output_dir,
                        source_language=config.source_language,
                        target_language=config.target_language,
                        subfolder=config.bilingual_subfolder,
                        dedup_when_same=config.bilingual_dedup_when_same,
                    )
                    bilingual_outputs.append(bilingual_path)
            else:
                translated_path = write_translated_epub(
                    parsed.document,  # type: ignore[arg-type]
                    per_file_translations,
                    config.output_dir,
                    target_language=config.target_language,
                )
                translated_outputs.append(translated_path)
                if config.bilingual_enabled:
                    bilingual_path = write_bilingual_epub(
                        parsed.document,  # type: ignore[arg-type]
                        per_file_translations,
                        config.output_dir,
                        source_language=config.source_language,
                        target_language=config.target_language,
                        subfolder=config.bilingual_subfolder,
                        dedup_when_same=config.bilingual_dedup_when_same,
                    )
                    bilingual_outputs.append(bilingual_path)
        except Exception as exc:  # pragma: no cover — writer-level failure is rare
            failed_files.append(
                FailedFile(
                    path=str(parsed.document.path),  # type: ignore[union-attr]
                    reason=f"{type(exc).__name__}: {exc}",
                    code="writer_error",
                    details={"error_type": type(exc).__name__},
                )
            )
            continue

        if missing_for_file:
            failed_files.append(
                FailedFile(
                    path=str(parsed.document.path),  # type: ignore[union-attr]
                    reason=f"{missing_for_file} segments missing from translation results",
                    code="missing_translations",
                    details={"missing_segments": missing_for_file},
                )
            )

    return translated_outputs, bilingual_outputs, failed_files


RunnerFactory = Callable[[LlmClient, TranslationConfig], SubtaskRunner]
ClockFn = Callable[[], str]
IdFactory = Callable[[], str]


def _default_runner_factory(
    client: LlmClient, config: TranslationConfig
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
    return TranslationSubtaskRunner(
        client=client,
        model=config.model,
        prompt_preset=config.prompt_preset,
        source_language=config.source_language,
        target_language=config.target_language,
        post_replacements=tuple(config.post_replacements),
        enable_confidence_check=config.enable_confidence_check,
        min_length_ratio=config.min_length_ratio,
        max_length_ratio=config.max_length_ratio,
        max_punctuation_delta=config.max_punctuation_delta,
        low_confidence_max_retries=config.low_confidence_max_retries,
        tpm_limiter=tpm_limiter,
        key_pool=key_pool,
        stream=config.stream,
        debug_log_dir=config.debug_log_dir,
        fake_name_roster=config.fake_name_roster,
        solo_retry_limiter=asyncio.Semaphore(
            max(1, min(4, effective_concurrency_limit(config.model)))
        ),
    )


def _default_id_factory() -> str:
    return f"translation-{uuid4().hex[:12]}"


__all__ = [
    "TranslationOrchestrator",
    "TranslationRunResult",
]
