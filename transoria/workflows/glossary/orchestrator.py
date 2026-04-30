"""Glossary extraction orchestrator: scan → parse → extract → normalize → emit.

The pipeline mirrors the translation orchestrator but emits per-file
artifacts instead of writing translated documents:

1. Scan + parse the input directory (TXT + EPUB).
2. Build per-file source segment lists (whitespace-only segments dropped).
3. Build chunks (char-bounded, never splitting a segment).
4. Seed one task with one subtask per chunk.
5. Run the executor — each subtask sends its chunk to the LLM and persists
   the decoded `{src, dst, info}` candidates and any decode issues.
6. After settle: aggregate candidates per source file, run normalization,
   then frequency + reference scan, then write the three artifacts.
7. Write the run statistics file.

A failed chunk does not halt the run; its candidates are simply absent. The
file the chunk belongs to is recorded under ``failed_files`` so the UI can
surface it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from transoria.domain import SubtaskStatus, TaskKind, TaskStatus
from transoria.formats.epub_parser import parse_epub_file
from transoria.formats.scanner import scan_input_directory
from transoria.formats.text import parse_txt_file
from transoria.llm.client import LlmClient
from transoria.llm.decoders import GlossaryEntry
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
from transoria.workflows.glossary.candidate import Candidate, GlossaryRecord
from transoria.workflows.glossary.chunker import (
    GlossaryChunk,
    build_glossary_chunks,
)
from transoria.workflows.glossary.combine import combine_glossary_records
from transoria.workflows.glossary.config import GlossaryConfig
from transoria.workflows.glossary.exporters import (
    glossary_basename,
    purge_glossary_artifacts,
    write_glossary_artifacts,
    write_glossary_decode_issues,
)
from transoria.workflows.glossary.frequency import (
    count_frequencies_and_references,
)
from transoria.workflows.glossary.normalize import normalize_candidates
from transoria.workflows.glossary.runner import (
    GlossarySubtaskRunner,
    decode_glossary_subtask_response,
    encode_glossary_payload,
)
from transoria.workflows.glossary.statistics import (
    GlossaryFailedFile,
    GlossaryStatistics,
    write_glossary_statistics,
)


@dataclass(frozen=True)
class GlossaryArtifactSet:
    novel_name: str
    xlsx_path: Path
    json_path: Path
    references_path: Path
    source_path: Path | None = None
    decode_issue_path: Path | None = None
    is_combined: bool = False

    def __iter__(self):
        yield self.xlsx_path
        yield self.json_path
        yield self.references_path


@dataclass(frozen=True)
class GlossaryExtractionResult:
    task_id: str
    statistics: GlossaryStatistics
    statistics_path: Path
    glossary_outputs_per_file: tuple[GlossaryArtifactSet, ...]
    final_status: TaskStatus
    combined_output: GlossaryArtifactSet | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


RunnerFactory = Callable[[LlmClient, GlossaryConfig], SubtaskRunner]
ClockFn = Callable[[], str]
IdFactory = Callable[[], str]


def _default_runner_factory(
    client: LlmClient, config: GlossaryConfig
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
    return GlossarySubtaskRunner(
        client=client,
        model=config.model,
        prompt_preset=config.prompt_preset,
        source_language=config.source_language,
        target_language=config.target_language,
        tpm_limiter=tpm_limiter,
        key_pool=key_pool,
        stream=config.stream,
        debug_log_dir=config.debug_log_dir,
        fake_name_session=config.fake_name_session,
        name_injections=config.name_injections,
    )


def _default_id_factory() -> str:
    return f"glossary-{uuid4().hex[:12]}"


@dataclass
class GlossaryOrchestrator:
    cache: TaskCache
    client: LlmClient
    runner_factory: RunnerFactory = field(default_factory=lambda: _default_runner_factory)
    progress: ProgressListener | None = None
    clock: ClockFn = _utc_now_iso
    id_factory: IdFactory = field(default_factory=lambda: _default_id_factory)
    on_executor_created: "Callable[[TaskExecutor], None] | None" = None
    on_result_finalized: "Callable[[GlossaryExtractionResult], None] | None" = None

    async def run(self, config: GlossaryConfig) -> GlossaryExtractionResult:
        started_at = self.clock()
        source_segments_by_file = _scan_and_parse(config.input_dir)

        if not source_segments_by_file:
            return self._finalize_empty(config, started_at)

        # Sweep stale artifacts so the new run's outputs aren't sitting
        # next to first-run files with mismatched timestamps.
        combined_basename = (
            (config.input_dir.resolve().name or "Combined")
            if config.combine_folder_glossary
            else None
        )
        purge_glossary_artifacts(
            config.output_dir,
            source_paths=tuple(source_segments_by_file.keys()),
            combined_basename=combined_basename,
        )

        chunks = build_glossary_chunks(
            source_segments_by_file,
            chunk_char_limit=config.chunk_char_limit,
            chunk_token_limit=config.chunk_token_limit,
            token_counter=config.token_counter,
        )

        if not chunks:
            return self._finalize_empty(config, started_at)

        task_id = self.id_factory()
        # "Continue path" means subtasks were already seeded. A bare
        # TaskRecord without subtasks (e.g. a placeholder seeded by
        # TaskService for early read_snapshot reads) is "fresh start".
        existing_subtasks = (
            self.cache.load_subtasks(task_id) if self.cache.has_task(task_id) else ()
        )
        if existing_subtasks:
            # Continue path: reset FAILED → PENDING; preserve subtask
            # ids so chunk_id matches the original parse.
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
                kind=TaskKind.GLOSSARY,
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
                },
            )

            subtasks = [
                Subtask(
                    id=chunk.chunk_id,
                    task_id=task_id,
                    request_payload=encode_glossary_payload(chunk),
                )
                for chunk in chunks
            ]
            self.cache.write_seed(record, subtasks)

        runner = self.runner_factory(self.client, config)
        executor = TaskExecutor(
            cache=self.cache,
            runner=runner,
            concurrency_limit=max(1, config.model.concurrency_limit),
            rpm_limit=max(0, config.model.rpm_limit),
            progress=self.progress,
            clock=self.clock,
        )
        if self.on_executor_created is not None:
            self.on_executor_created(executor)

        snapshot = await executor.run(task_id)

        candidates_by_file, issues_by_file = _aggregate_candidates(
            snapshot.subtasks, chunks
        )

        glossary_outputs_per_file: list[GlossaryArtifactSet] = []
        failed_files: list[GlossaryFailedFile] = []
        all_records: list[GlossaryRecord] = []
        per_file_record_groups: list[tuple[GlossaryRecord, ...]] = []
        per_file_record_count = 0

        for source_file, segments in source_segments_by_file.items():
            # Always emit decode issues if any — even when nothing else
            # was extracted, the user needs to see why parsing failed.
            file_issues = issues_by_file.get(source_file, ())
            decode_issue_path: "Path | None" = None
            if file_issues:
                decode_issue_path = write_glossary_decode_issues(
                    file_issues,
                    config.output_dir,
                    basename=glossary_basename(source_file),
                )

            raw_entries = candidates_by_file.get(source_file, ())
            if not raw_entries:
                failed_files.append(
                    GlossaryFailedFile(
                        path=str(source_file),
                        reason="no glossary candidates extracted",
                    )
                )
                continue
            normalized = normalize_candidates(
                raw_entries,
                max_term_display_length=config.max_term_display_length,
                info_blacklist=config.info_blacklist,
                allow_src_eq_dst=config.allow_src_eq_dst,
                target_language=config.target_language,
                normalize_widths=config.normalize_widths,
            )
            records = count_frequencies_and_references(
                normalized,
                segments,
                reference_example_limit=config.reference_example_limit,
                min_frequency=config.min_frequency,
            )
            if not records:
                failed_files.append(
                    GlossaryFailedFile(
                        path=str(source_file),
                        reason="no entries survived frequency filter",
                    )
                )
                continue
            xlsx_path, json_path, references_path = write_glossary_artifacts(
                records,
                config.output_dir,
                source_path=source_file,
            )
            glossary_outputs_per_file.append(
                GlossaryArtifactSet(
                    novel_name=source_file.stem,
                    xlsx_path=xlsx_path,
                    json_path=json_path,
                    references_path=references_path,
                    source_path=source_file,
                    decode_issue_path=decode_issue_path,
                )
            )
            all_records.extend(records)
            per_file_record_groups.append(records)
            per_file_record_count += len(records)

        combined_output: GlossaryArtifactSet | None = None
        if config.combine_folder_glossary and len(per_file_record_groups) > 1:
            combined = combine_glossary_records(
                per_file_record_groups,
                reference_example_limit=config.reference_example_limit,
            )
            if combined:
                # Use the input folder's name as the basename for the combined
                # artifact set. Falls back to "Combined" when the input dir
                # has no descriptive name (e.g. ``/`` or ``.``).
                folder_name = config.input_dir.resolve().name or "Combined"
                xlsx_path, json_path, references_path = write_glossary_artifacts(
                    combined,
                    config.output_dir,
                    basename=folder_name,
                )
                combined_output = GlossaryArtifactSet(
                    novel_name=folder_name,
                    xlsx_path=xlsx_path,
                    json_path=json_path,
                    references_path=references_path,
                    source_path=None,
                    decode_issue_path=None,
                    is_combined=True,
                )

        candidate_count = sum(len(items) for items in candidates_by_file.values())
        decode_issue_count = sum(len(items) for items in issues_by_file.values())

        statistics = GlossaryStatistics(
            started_at=started_at,
            ended_at=self.clock(),
            processed_files=tuple(
                str(path) for path in source_segments_by_file.keys()
            ),
            glossary_outputs=tuple(
                str(path)
                for artifact in (
                    *glossary_outputs_per_file,
                    *(() if combined_output is None else (combined_output,)),
                )
                for path in artifact
            ),
            candidate_count=candidate_count,
            final_entry_count=per_file_record_count,
            failed_subtasks=sum(
                1 for s in snapshot.subtasks if s.status is SubtaskStatus.FAILED
            ),
            failed_files=tuple(failed_files),
            decode_issue_count=decode_issue_count,
            usage=snapshot.usage(),
        )
        failed_subtask_details = tuple(
            (s.id, s.last_error)
            for s in snapshot.subtasks
            if s.status is SubtaskStatus.FAILED and s.last_error
        )
        statistics_path = write_glossary_statistics(
            statistics,
            config.output_dir,
            failed_subtask_details=failed_subtask_details,
        )

        result = GlossaryExtractionResult(
            task_id=task_id,
            statistics=statistics,
            statistics_path=statistics_path,
            glossary_outputs_per_file=tuple(glossary_outputs_per_file),
            combined_output=combined_output,
            final_status=snapshot.record.status,
        )
        if self.on_result_finalized is not None:
            self.on_result_finalized(result)
        return result

    def _finalize_empty(
        self, config: GlossaryConfig, started_at: str
    ) -> GlossaryExtractionResult:
        statistics = GlossaryStatistics(
            started_at=started_at, ended_at=self.clock()
        )
        statistics_path = write_glossary_statistics(statistics, config.output_dir)
        result = GlossaryExtractionResult(
            task_id="",
            statistics=statistics,
            statistics_path=statistics_path,
            glossary_outputs_per_file=(),
            combined_output=None,
            final_status=TaskStatus.COMPLETED,
        )
        if self.on_result_finalized is not None:
            self.on_result_finalized(result)
        return result


# ---------------------------------------------------------------------------
# Scan + parse
# ---------------------------------------------------------------------------


def _scan_and_parse(input_dir: Path) -> dict[Path, tuple[str, ...]]:
    """Parse every supported file and return its non-empty source segments.

    The dict is ordered by the scanner's deterministic order so chunk ids,
    output filenames, and the statistics file are reproducible across runs.
    """

    discovered = scan_input_directory(input_dir)
    result: dict[Path, tuple[str, ...]] = {}
    for document_file in discovered:
        if document_file.format.value == "txt":
            doc = parse_txt_file(document_file.path)
            result[document_file.path] = tuple(
                segment.text for segment in doc.segments if segment.text.strip()
            )
        else:
            doc = parse_epub_file(document_file.path)
            result[document_file.path] = tuple(
                segment.text for segment in doc.segments if segment.text.strip()
            )
    return result


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def _aggregate_candidates(
    subtasks: tuple[Subtask, ...],
    chunks: tuple[GlossaryChunk, ...],
) -> tuple[
    Mapping[Path, tuple[GlossaryEntry, ...]],
    Mapping[Path, tuple[Mapping[str, str], ...]],
]:
    """Collect candidates from completed subtasks, attributed to source files."""

    chunk_to_file = {chunk.chunk_id: chunk.source_file for chunk in chunks}
    candidates: dict[Path, list[GlossaryEntry]] = defaultdict(list)
    issues: dict[Path, list[Mapping[str, str]]] = defaultdict(list)
    for subtask in subtasks:
        if subtask.status is not SubtaskStatus.COMPLETED:
            continue
        source_file = chunk_to_file.get(subtask.id)
        if source_file is None:
            continue
        decoded_entries, decoded_issues = decode_glossary_subtask_response(
            subtask.response_content
        )
        candidates[source_file].extend(decoded_entries)
        issues[source_file].extend(decoded_issues)
    return (
        {path: tuple(items) for path, items in candidates.items()},
        {path: tuple(items) for path, items in issues.items()},
    )


__all__ = ["GlossaryArtifactSet", "GlossaryExtractionResult", "GlossaryOrchestrator"]
