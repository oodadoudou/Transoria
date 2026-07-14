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
- After the executor + split-failed-chunks loop finishes the run settles to a
  terminal status immediately. Execution/integrity failures stay continuable;
  complete responses with quality warnings finish successfully and surface the
  exact rows in proofreading. Transient transport errors are absorbed by
  request-level backoff retries, not by whole-task recovery rounds.
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
from transoria.workflows.executor_pacing import llm_launch_spacing_seconds
from transoria.workflows.prefilter import should_translate_for_language
from transoria.workflows.translation.glossary_report import (
    build_glossary_application_report,
    write_glossary_application_report,
)
from transoria.workflows.translation.preprocessor import (
    preprocess_segment,
    strip_drm_invisibles,
    strip_protection_sentinels,
)
from transoria.workflows.translation.runner import (
    RECOVERY_SEGMENT_IDS_KEY,
    TranslationRecoveryRunner,
    TranslationSubtaskRunner,
    encode_subtask_payload,
)
from transoria.workflows.translation.segment_state import (
    ACCEPTED_OVERRIDE_SEGMENTS_KEY,
    collect_segment_state_from_authoritative_subtasks,
    low_confidence_by_segment,
)
from transoria.workflows.translation.statistics import (
    FailedFile,
    LowConfidenceSegment,
    TranslationStatistics,
    write_translation_statistics,
)


# One split round only for failures with explicit source-residue evidence.
# Other failures keep the original chunk intact for a user-triggered Continue.
_SPLIT_ROUNDS = 1
_SOURCE_RESIDUE_RECOVERY_REASONS = {
    "fell_back_to_source_after_max_retries",
    "missing_translation_fell_back_to_source",
    "mass_source_residue_after_retry",
}


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
    glossary_report_path: Path | None
    glossary_report_json_path: Path | None
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
            _prepare_segment_recovery(self.cache, task_id, existing_subtasks)
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
        runner = TranslationRecoveryRunner(self.runner_factory(self.client, config))
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
            # Request-level timeout/retry already bounds LLM calls; a second
            # aggregate cap can fail valid retry/rescue chains mid-flight.
            subtask_timeout_seconds=0.0,
            launch_spacing_seconds=llm_launch_spacing_seconds(actual_concurrency),
        )
        if self.on_executor_created is not None:
            self.on_executor_created(executor)

        snapshot = await executor.run(task_id)
        # The split-failed-chunks loop must respect the stop signal —
        # otherwise pressing Stop and waiting for in-flight requests to
        # drain would just be followed by a split-rerun, defeating the
        # user's intent. After it finishes the run settles to a terminal
        # status right away; leftover FAILED subtasks stay continuable
        # via Continue / proofreading rather than a whole-task auto-retry
        # storm. Transient transport errors are handled by request-level
        # backoff, not by re-running the whole task.
        while (
            not executor.is_stopping
            and self._split_failed_subtasks(task_id, snapshot.subtasks, config)
        ):
            snapshot = await executor.run(task_id)

        if (
            snapshot.record.status is TaskStatus.COMPLETED
            and _has_systemic_source_residue(snapshot.subtasks)
        ):
            self._mark_task_status(task_id, TaskStatus.FAILED)
            snapshot = self.cache.load(task_id)

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
        glossary_report = build_glossary_application_report(
            snapshot.subtasks,
            translations_by_segment,
        )
        glossary_report_path: Path | None = None
        glossary_report_json_path: Path | None = None
        if glossary_report.total_matches:
            report_paths = write_glossary_application_report(
                glossary_report,
                self.cache.task_dir(task_id),
            )
            glossary_report_path = report_paths.markdown_path
            glossary_report_json_path = report_paths.json_path

        result = TranslationRunResult(
            task_id=task_id,
            statistics=statistics,
            statistics_path=statistics_path,
            glossary_report_path=glossary_report_path,
            glossary_report_json_path=glossary_report_json_path,
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
            glossary_report_path=None,
            glossary_report_json_path=None,
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
            if not _should_split_failed_subtask(subtask):
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


def _should_split_failed_subtask(subtask: Subtask) -> bool:
    if subtask.request_payload.get(RECOVERY_SEGMENT_IDS_KEY):
        return False
    if _is_model_or_request_failure(subtask.last_error):
        return False
    return bool(_source_residue_segment_ids(_decode_subtask_payload(subtask)))


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
            unprotected_prompt_text = strip_protection_sentinels(
                preprocessed.prompt_text
            )
            if not should_translate_for_language(
                unprotected_prompt_text,
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


def _decode_subtask_payload(subtask: Subtask) -> dict[str, object]:
    if not subtask.response_content:
        return {}
    try:
        payload = json.loads(subtask.response_content)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _is_model_or_request_failure(last_error: str) -> bool:
    stripped = last_error.strip()
    if stripped.startswith("["):
        closing = stripped.find("]")
        if closing > 1:
            code = stripped[1:closing]
            if code.startswith("llm.") or code.startswith("runtime."):
                return True
    lowered = stripped.lower()
    return any(
        marker in lowered
        for marker in (
            "http 4",
            "http 5",
            "requesterror",
            "transporterror",
            "timeouterror",
            "connectionerror",
            "noapikeyerror",
            "api key",
            "all keys failed",
            "malformed response",
            "invalid json",
            "auth failure",
        )
    )


def _source_residue_segment_ids(payload: Mapping[str, object]) -> set[str]:
    return {
        segment_id
        for segment_id, record in low_confidence_by_segment(payload).items()
        if _is_recoverable_source_residue(record)
    }


def _has_systemic_source_residue(subtasks: tuple[Subtask, ...]) -> bool:
    for subtask in subtasks:
        if subtask.status is not SubtaskStatus.COMPLETED:
            continue
        for record in low_confidence_by_segment(
            _decode_subtask_payload(subtask)
        ).values():
            reasons = record.get("reasons", [])
            if "mass_source_residue_after_retry" in reasons:
                return True
    return False


def _is_recoverable_source_residue(record: Mapping[str, object]) -> bool:
    raw_tags = record.get("tags", [])
    tags = {str(tag) for tag in raw_tags} if isinstance(raw_tags, list) else set()
    raw_reasons = record.get("reasons", [])
    reasons = (
        {str(reason) for reason in raw_reasons}
        if isinstance(raw_reasons, list)
        else set()
    )
    return "source_residue" in tags and bool(
        _SOURCE_RESIDUE_RECOVERY_REASONS.intersection(reasons)
    )


def _segment_recovery_candidates(subtasks: tuple[Subtask, ...]) -> set[str]:
    accepted: set[str] = set()
    for subtask in subtasks:
        payload = _decode_subtask_payload(subtask)
        raw_accepted = payload.get(ACCEPTED_OVERRIDE_SEGMENTS_KEY, [])
        if isinstance(raw_accepted, list):
            accepted.update(
                str(item) for item in raw_accepted if item not in (None, "")
            )

    candidates: set[str] = set()
    for subtask in subtasks:
        if subtask.status not in {
            SubtaskStatus.COMPLETED,
            SubtaskStatus.FAILED,
        }:
            continue
        if (
            subtask.status is SubtaskStatus.FAILED
            and _is_model_or_request_failure(subtask.last_error)
        ):
            continue
        candidates.update(_source_residue_segment_ids(_decode_subtask_payload(subtask)))
    return candidates - accepted


def _prepare_segment_recovery(
    cache: TaskCache,
    task_id: str,
    subtasks: tuple[Subtask, ...],
) -> None:
    normalized: list[Subtask] = []
    for subtask in subtasks:
        settled = _settle_complete_quality_recovery(subtask)
        if settled is not subtask:
            cache.save_subtask(settled)
        normalized.append(settled)

    prepared_subtasks = tuple(normalized)
    candidates = _segment_recovery_candidates(prepared_subtasks)
    owners = _recovery_owners(prepared_subtasks, candidates)

    for subtask in prepared_subtasks:
        segment_ids = owners.get(subtask.id)
        if segment_ids:
            request_payload = dict(subtask.request_payload)
            request_payload[RECOVERY_SEGMENT_IDS_KEY] = sorted(
                segment_ids,
                key=lambda value: tuple(int(part) for part in value.split(":")),
            )
            cache.save_subtask(
                replace(
                    subtask,
                    status=SubtaskStatus.PENDING,
                    request_payload=request_payload,
                    last_error="",
                    last_error_at="",
                )
            )
        elif subtask.status is SubtaskStatus.FAILED:
            request_payload = dict(subtask.request_payload)
            request_payload.pop(RECOVERY_SEGMENT_IDS_KEY, None)
            cache.save_subtask(
                replace(
                    subtask,
                    status=SubtaskStatus.PENDING,
                    request_payload=request_payload,
                    last_error="",
                    last_error_at="",
                )
            )


def _settle_complete_quality_recovery(subtask: Subtask) -> Subtask:
    """Upgrade legacy quality-only failures without issuing another request."""

    if subtask.status is not SubtaskStatus.FAILED or not subtask.last_error.startswith(
        "[translation.segment_recovery_failed]"
    ):
        return subtask
    if _is_model_or_request_failure(subtask.last_error):
        return subtask
    payload = _decode_subtask_payload(subtask)
    raw_translations = payload.get("translations")
    if not isinstance(raw_translations, Mapping):
        return subtask
    requested_ids = {
        str(segment.get("segment_id", ""))
        for segment in subtask.request_payload.get("segments", [])
        if isinstance(segment, Mapping)
        and segment.get("segment_id") not in (None, "")
    }
    if not requested_ids or any(
        segment_id not in raw_translations
        or not isinstance(raw_translations[segment_id], str)
        or not str(raw_translations[segment_id]).strip()
        for segment_id in requested_ids
    ):
        return subtask
    request_payload = dict(subtask.request_payload)
    request_payload.pop(RECOVERY_SEGMENT_IDS_KEY, None)
    return replace(
        subtask,
        status=SubtaskStatus.COMPLETED,
        request_payload=request_payload,
        last_error="",
        last_error_at="",
    )


def _recovery_owners(
    subtasks: tuple[Subtask, ...], candidates: set[str]
) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for segment_id in candidates:
        owner: Subtask | None = None
        for subtask in reversed(subtasks):
            raw_segments = subtask.request_payload.get("segments", [])
            if not isinstance(raw_segments, list):
                continue
            if not any(
                isinstance(segment, Mapping)
                and str(segment.get("segment_id", "")) == segment_id
                for segment in raw_segments
            ):
                continue
            if subtask.status is SubtaskStatus.SKIPPED:
                continue
            owner = subtask
            if subtask.status in {SubtaskStatus.COMPLETED, SubtaskStatus.FAILED}:
                break
        if owner is not None:
            owners.setdefault(owner.id, []).append(segment_id)
    return owners


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

    translations, low_confidence_by_segment = (
        collect_segment_state_from_authoritative_subtasks(subtasks)
    )
    low_confidence = [
        {
            "segment_id": segment_id,
            "reasons": list(record.get("reasons", [])),
        }
        for segment_id, record in low_confidence_by_segment.items()
    ]
    return translations, low_confidence


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
        transport_retry_attempts=config.request_retry_attempts,
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
