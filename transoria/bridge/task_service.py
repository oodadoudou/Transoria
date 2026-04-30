"""Bridge-facing orchestration of long-running tasks.

This module is the single owner of:

- ``TaskRegistry`` — in-memory map of in-flight tasks.
- ``TaskCache`` — file-backed task records under ``<cache_root>/tasks/``.
- Translation / Glossary / Replacement launches: validate settings, build the
  workflow config, run the async orchestrator (or sync replacement loop) on a
  background thread, and persist artifact metadata for ``read_artifacts``.

The bridge handlers are thin shells around :class:`TaskService` so the bridge
contract surface stays declarative and the runtime wiring lives in one place.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import traceback
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from transoria.bridge.errors import BridgeError
from transoria.bridge.task_registry import RunningTask, TaskRegistry
from transoria.domain import (
    DocumentFormat,
    Language,
    SubtaskStatus,
    TaskKind,
    TaskStatus,
)
from transoria.formats.scanner import scan_input_directory
from transoria.llm.client import HttpxChatTransport, LlmClient
from transoria.llm.config import ModelConfig
from transoria.model_profiles import ModelProfileStore
from transoria.prompts import (
    PromptKind,
    PromptPreset,
    PromptPresetStore,
)
from transoria.runtime.cache import TaskCache, TaskNotFoundError
from transoria.runtime.executor import TaskExecutor
from transoria.runtime.subtask import Subtask
from transoria.runtime.task_record import TaskRecord, TaskSnapshot
from transoria.settings import SettingsStore
from transoria.tools.replacement import (
    REPLACED_SUFFIX,
    ReplacementRule,
    replace_epub_file,
    replace_txt_file,
)
from transoria.workflows.glossary.config import GlossaryConfig
from transoria.workflows.translation.rules import (
    Glossary,
    ReplacementRule as TranslationReplacementRule,
    TextPreserveRule,
)
from transoria.workflows.glossary.exporters import (
    GLOSSARY_FILENAME_DECODE_ISSUES,
    GLOSSARY_FILENAME_JSON,
    GLOSSARY_FILENAME_REFERENCES,
    GLOSSARY_FILENAME_XLSX,
)
from transoria.workflows.glossary.orchestrator import (
    GlossaryArtifactSet,
    GlossaryExtractionResult,
    GlossaryOrchestrator,
)
from transoria.workflows.glossary.statistics import GLOSSARY_STATISTICS_FILENAME_JSON
from transoria.workflows.translation.config import TranslationConfig
from transoria.workflows.translation.orchestrator import (
    TranslationOrchestrator,
    TranslationRunResult,
)
from transoria.workflows.translation.statistics import STATISTICS_FILENAME_JSON

LlmClientFactory = Callable[[], LlmClient]


_RESULT_FILENAME = "result.json"
_KIND_TO_TASKKIND: dict[str, TaskKind] = {
    "translation": TaskKind.TRANSLATION,
    "glossary": TaskKind.GLOSSARY,
    "replacement": TaskKind.REPLACEMENT,
}

# Persisted statuses that imply a live executor must exist; if the
# in-memory registry is empty for one of these, the host crashed mid-run.
_ZOMBIE_TASK_STATES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.RUNNING, TaskStatus.STOPPING, TaskStatus.PAUSING}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_task_id(kind: str) -> str:
    return f"{kind}-{uuid4().hex[:12]}"


def _coerce_text_preserve_rules(
    raw: Sequence[Mapping[str, object]],
) -> tuple[TextPreserveRule, ...]:
    """Build immutable ``TextPreserveRule`` tuple from settings.

    Bad rows (empty pattern) are dropped silently — the UI's
    pattern editor enforces non-empty values, so anything reaching
    this path is either a hand-edited JSON or a stale entry.
    """

    rules: list[TextPreserveRule] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        pattern = str(entry.get("pattern", "")).strip()
        if not pattern:
            continue
        rules.append(
            TextPreserveRule(
                pattern=pattern,
                note=str(entry.get("note", "")),
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return tuple(rules)


def _coerce_translation_replacements(
    raw: Sequence[Mapping[str, object]],
) -> tuple[TranslationReplacementRule, ...]:
    """Build immutable translation ``ReplacementRule`` tuple from
    settings entries. Distinct from ``transoria.tools.replacement.
    ReplacementRule`` — translation rules carry a ``note`` field for
    user annotation."""

    rules: list[TranslationReplacementRule] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        src = str(entry.get("src", "")).strip()
        if not src:
            continue
        rules.append(
            TranslationReplacementRule(
                src=src,
                dst=str(entry.get("dst", "")),
                regex=bool(entry.get("regex", False)),
                case_sensitive=bool(entry.get("case_sensitive", False)),
                note=str(entry.get("note", "")),
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return tuple(rules)


def _coerce_language(value: str, *, field: str) -> Language:
    try:
        return Language(value)
    except ValueError as exc:
        raise BridgeError.invalid_argument(
            f"unsupported language {value!r} for {field}.",
            field=field,
        ) from exc


def _require_directory(value: str, *, field: str) -> Path:
    if not value:
        raise BridgeError.invalid_argument(
            f"{field} is required.",
            field=field,
        )
    path = Path(value)
    if not path.exists():
        raise BridgeError.invalid_argument(
            f"{field} does not exist: {value!r}",
            field=field,
        )
    if not path.is_dir():
        raise BridgeError.invalid_argument(
            f"{field} is not a directory: {value!r}",
            field=field,
        )
    return path


def _require_input_with_supported_files(path: Path, *, field: str) -> None:
    """Reject empty / unsupported-only input folders before a task starts.

    Translation and glossary cannot do useful work on a folder that
    contains no ``.epub`` / ``.txt`` files; without this check the task
    seeds, runs zero subtasks, and reports COMPLETED with no output —
    which looks like a silent failure to the user. Raise a typed bridge
    error so the frontend's existing error banner surfaces a clear
    message instead.
    """

    if not scan_input_directory(path):
        raise BridgeError.invalid_argument(
            f"{field} contains no supported files (.epub, .txt): {path!s}",
            field=field,
        )


def _ensure_output_dir(value: str, *, field: str) -> Path:
    if not value:
        raise BridgeError.invalid_argument(
            f"{field} is required.",
            field=field,
        )
    path = Path(value)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BridgeError(
            "bridge.io_error",
            f"cannot create {field}: {exc}",
            retryable=False,
            details={"field": field, "path": value},
        ) from exc
    return path


# ---------------------------------------------------------------------------
# Snapshot serialization (cache record → wire shape)
# ---------------------------------------------------------------------------


def _format_header(record: TaskRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "kind": record.kind.value,
        "status": record.status.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _progress_to_block(progress) -> dict[str, object]:
    return {
        "total": progress.total,
        "pending": progress.pending,
        "running": progress.running,
        "completed": progress.completed,
        "failed": progress.failed,
        "skipped": progress.skipped,
        "rate_per_second": progress.rate_per_second,
        "eta_seconds": progress.eta_seconds,
    }


def _usage_to_block(usage) -> dict[str, object]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.input_tokens + usage.output_tokens,
    }


def _format_snapshot(snapshot: TaskSnapshot) -> dict[str, object]:
    progress = snapshot.progress()
    usage = snapshot.usage()
    metadata = dict(snapshot.record.metadata)
    # When subtasks/ has been pruned post-completion (see
    # ``_maybe_cleanup_cache``), the live progress recomputes to 0/0;
    # surface the frozen totals stashed in metadata instead so the Run
    # page keeps showing the completed run's final stats until the user
    # starts a fresh task.
    if len(snapshot.subtasks) == 0:
        frozen_progress = metadata.get("final_progress")
        if isinstance(frozen_progress, dict):
            progress_block = {
                "total": int(frozen_progress.get("total", 0)),
                "pending": int(frozen_progress.get("pending", 0)),
                "running": int(frozen_progress.get("running", 0)),
                "completed": int(frozen_progress.get("completed", 0)),
                "failed": int(frozen_progress.get("failed", 0)),
                "skipped": int(frozen_progress.get("skipped", 0)),
                "rate_per_second": 0.0,
                "eta_seconds": 0.0,
            }
        else:
            progress_block = _progress_to_block(progress)
        frozen_usage = metadata.get("final_usage")
        if isinstance(frozen_usage, dict):
            input_t = int(frozen_usage.get("input_tokens", 0))
            output_t = int(frozen_usage.get("output_tokens", 0))
            usage_block = {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "total_tokens": input_t + output_t,
            }
        else:
            usage_block = _usage_to_block(usage)
    else:
        progress_block = _progress_to_block(progress)
        usage_block = _usage_to_block(usage)
    return {
        "header": _format_header(snapshot.record),
        "progress": progress_block,
        "usage": usage_block,
        # Per-chunk status drives the chunk-grid UX. Tuple-of-objects
        # is preserved in the order the orchestrator seeded them, so
        # the grid renders chunk-0 leftmost.
        "subtasks": [
            {"id": s.id, "status": s.status.value} for s in snapshot.subtasks
        ],
        "active_model_id": metadata.get("model_id"),
        "active_prompt_id": metadata.get("prompt_preset_id"),
        "metadata": metadata,
    }


def _format_failures(snapshot: TaskSnapshot) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for subtask in snapshot.subtasks:
        if subtask.status is not SubtaskStatus.FAILED:
            continue
        last_error = subtask.last_error or ""
        code = ""
        message = last_error
        if last_error.startswith("[") and "] " in last_error:
            close = last_error.index("] ")
            code = last_error[1:close]
            message = last_error[close + 2 :]
        source_file = ""
        payload = subtask.request_payload
        if isinstance(payload, Mapping):
            for key in ("source_file", "file_path", "path"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    source_file = value
                    break
        failures.append(
            {
                "subtask_id": subtask.id,
                "source_file": source_file,
                "message": message,
                "attempts": subtask.attempt_count,
                "last_error_code": code,
                "last_error_at": subtask.last_error_at,
            }
        )
    return failures


# ---------------------------------------------------------------------------
# Result payload formatters
# ---------------------------------------------------------------------------


def _translation_result_payload(
    result: TranslationRunResult, *, config: TranslationConfig
) -> dict[str, object]:
    statistics = result.statistics
    bilingual_folder: str | None = None
    if result.bilingual_outputs:
        bilingual_folder = str(config.output_dir / config.bilingual_subfolder)
    return {
        "kind": "translation",
        "output_folder": str(config.output_dir),
        "bilingual_folder": bilingual_folder,
        "translated_files": [str(p) for p in result.translated_outputs],
        "bilingual_files": [str(p) for p in result.bilingual_outputs],
        "statistics_json_path": str(result.statistics_path)
        if result.statistics_path
        else None,
        "processed_files": list(statistics.processed_files),
        "completed_segments": statistics.completed_segments,
        "total_segments": statistics.total_segments,
    }


def _glossary_result_payload(
    result: GlossaryExtractionResult, *, config: GlossaryConfig
) -> dict[str, object]:
    per_novel = [
        _glossary_artifact_payload(item)
        for item in result.glossary_outputs_per_file
    ]
    combined = (
        _glossary_artifact_payload(result.combined_output)
        if result.combined_output is not None
        else None
    )
    decode_paths = [
        str(item.decode_issue_path)
        for item in result.glossary_outputs_per_file
        if item.decode_issue_path is not None
    ]
    return {
        "kind": "glossary",
        "output_folder": str(config.output_dir),
        "per_novel_artifacts": per_novel,
        "combined_artifact": combined,
        "statistics_json_path": str(result.statistics_path)
        if result.statistics_path
        else None,
        "decode_issue_path": decode_paths[0] if decode_paths else None,
    }


def _glossary_artifact_payload(artifact: GlossaryArtifactSet) -> dict[str, object]:
    payload: dict[str, object] = {
        "novel_name": artifact.novel_name,
        "xlsx_path": str(artifact.xlsx_path),
        "json_path": str(artifact.json_path),
        "references_path": str(artifact.references_path),
    }
    if artifact.decode_issue_path is not None:
        payload["decode_issue_path"] = str(artifact.decode_issue_path)
    return payload


def _partial_translation_payload(
    snapshot: TaskSnapshot, *, output_dir: Path
) -> dict[str, object]:
    stats = _read_json_file(output_dir / STATISTICS_FILENAME_JSON)
    translated = _string_list(stats.get("translated_outputs")) if stats else []
    bilingual = _string_list(stats.get("bilingual_outputs")) if stats else []
    if not translated:
        translated = _scan_files(output_dir, exclude_dirs={"bilingual outputs"})
    if not bilingual:
        bilingual = _scan_bilingual_files(output_dir)
    progress = snapshot.progress()
    stats_path = output_dir / STATISTICS_FILENAME_JSON
    bilingual_folder = str(Path(bilingual[0]).parent) if bilingual else None
    return {
        "kind": "translation",
        "partial": True,
        "output_folder": str(output_dir),
        "bilingual_folder": bilingual_folder,
        "translated_files": translated,
        "bilingual_files": bilingual,
        "statistics_json_path": str(stats_path) if stats_path.exists() else None,
        "processed_files": _string_list(stats.get("processed_files"))
        if stats
        else [],
        "completed_segments": (
            int(stats.get("completed_segments", progress.completed))
            if stats
            else progress.completed
        ),
        "total_segments": (
            int(stats.get("total_segments", progress.total))
            if stats
            else progress.total
        ),
    }


def _partial_glossary_payload(
    *, output_dir: Path, input_folder_name: str, statistics_dir: Path
) -> dict[str, object]:
    artifacts: list[dict[str, object]] = []
    combined: dict[str, object] | None = None
    for xlsx_path in sorted(output_dir.glob(f"*{GLOSSARY_FILENAME_XLSX}")):
        basename = xlsx_path.name.removesuffix(GLOSSARY_FILENAME_XLSX)
        json_path = output_dir / f"{basename}{GLOSSARY_FILENAME_JSON}"
        references_path = output_dir / f"{basename}{GLOSSARY_FILENAME_REFERENCES}"
        decode_path = output_dir / f"{basename}{GLOSSARY_FILENAME_DECODE_ISSUES}"
        item: dict[str, object] = {
            "novel_name": basename,
            "xlsx_path": str(xlsx_path),
            "json_path": str(json_path) if json_path.exists() else None,
            "references_path": str(references_path)
            if references_path.exists()
            else None,
        }
        if decode_path.exists():
            item["decode_issue_path"] = str(decode_path)
        if input_folder_name and basename == input_folder_name:
            combined = item
        else:
            artifacts.append(item)
    decode_paths = [
        str(path)
        for path in sorted(output_dir.glob(f"*{GLOSSARY_FILENAME_DECODE_ISSUES}"))
    ]
    stats_path = statistics_dir / GLOSSARY_STATISTICS_FILENAME_JSON
    return {
        "kind": "glossary",
        "partial": True,
        "output_folder": str(output_dir),
        "per_novel_artifacts": artifacts,
        "combined_artifact": combined,
        "statistics_json_path": str(stats_path) if stats_path.exists() else None,
        "decode_issue_path": decode_paths[0] if decode_paths else None,
    }


def _partial_replacement_payload(
    snapshot: TaskSnapshot, *, output_dir: Path
) -> dict[str, object]:
    output_files: list[str] = []
    total_replacements = 0
    for subtask in snapshot.subtasks:
        if subtask.status is not SubtaskStatus.COMPLETED:
            continue
        payload = _loads_json_object(subtask.response_content)
        output_path = payload.get("output_path")
        if isinstance(output_path, str) and output_path:
            output_files.append(output_path)
        count = payload.get("replacement_count")
        if isinstance(count, int):
            total_replacements += count
    return {
        "kind": "replacement",
        "partial": True,
        "output_folder": str(output_dir),
        "output_files": output_files,
        "statistics_json_path": None,
        "total_replacements": total_replacements,
    }


def _read_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        return _loads_json_object(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _loads_json_object(raw: str) -> dict[str, object]:
    if not raw:
        return {}
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _scan_files(output_dir: Path, *, exclude_dirs: set[str]) -> list[str]:
    if not output_dir.exists():
        return []
    files: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".epub"}:
            continue
        if any(part in exclude_dirs for part in path.relative_to(output_dir).parts[:-1]):
            continue
        if path.name.startswith(("translation-statistics", "extraction-statistics")):
            continue
        files.append(str(path))
    return files


def _scan_bilingual_files(output_dir: Path) -> list[str]:
    if not output_dir.exists():
        return []
    files: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".epub"}:
            continue
        if path.parent == output_dir:
            continue
        files.append(str(path))
    return files


# ---------------------------------------------------------------------------
# TaskService
# ---------------------------------------------------------------------------


@dataclass
class TaskService:
    """Bridge-facing facade over the runtime + workflow orchestrators."""

    cache: TaskCache  # legacy fallback when settings.output_folder is unset
    registry: TaskRegistry
    settings_store: SettingsStore
    profile_store: ModelProfileStore
    prompts_cache_root: Path
    llm_client_factory: LlmClientFactory

    # ------------------------------------------------------------------
    # Per-task cache resolution
    # ------------------------------------------------------------------
    #
    # Task records (record.json + subtasks/) live under the user's
    # output folder so each book/run keeps its own progress cache and
    # the user can manually wipe runs they no longer want. Falls back
    # to the legacy app-level cache when the output folder is unset
    # (e.g. early test fixtures, smoke probes).

    _CACHE_DIRNAME = "transoria-cache"

    def _cache_for_kind(self, kind: str) -> TaskCache:
        settings = self.settings_store.load_all()
        if kind == "translation":
            output = settings.translation.output_folder
        elif kind == "glossary":
            output = settings.glossary.output_folder
        elif kind == "replacement":
            output = settings.replacement.output_folder
        else:
            return self.cache
        if not output:
            return self.cache
        return TaskCache(root=Path(output) / self._CACHE_DIRNAME)

    def _cache_for_task(self, task_id: str) -> TaskCache:
        if task_id.startswith("translation-"):
            return self._cache_for_kind("translation")
        if task_id.startswith("glossary-"):
            return self._cache_for_kind("glossary")
        if task_id.startswith("replacement-"):
            return self._cache_for_kind("replacement")
        return self.cache

    def _maybe_cleanup_cache(self, kind: str, task_id: str) -> None:
        """Drop the bulky ``subtasks/`` dir on clean success.

        ``Clean success`` = task status COMPLETED and zero failed
        subtasks. ``task.json`` + ``result.json`` are preserved so
        ``read_snapshot`` / ``read_artifacts`` can still surface the
        completed run; only the per-subtask payloads (which can be
        thousands of files for a long book) are deleted.

        Stopped / paused / failed tasks keep their full cache so the
        user can resume.
        """

        cache = self._cache_for_kind(kind)
        try:
            snapshot = cache.load(task_id)
        except (TaskNotFoundError, OSError, ValueError):
            return
        if snapshot.record.status is not TaskStatus.COMPLETED:
            return
        progress = snapshot.progress()
        if progress.failed > 0:
            return
        # Freeze the final progress + token totals into metadata before we
        # wipe subtasks/. Without this the next read_snapshot would re-derive
        # progress from an empty subtask list and surface 0/0 instead of
        # the completed run's stats.
        usage = snapshot.usage()
        frozen = dict(snapshot.record.metadata)
        frozen["final_progress"] = {
            "total": progress.total,
            "pending": progress.pending,
            "running": progress.running,
            "completed": progress.completed,
            "failed": progress.failed,
            "skipped": progress.skipped,
        }
        frozen["final_usage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
        cache.save_task(
            replace(
                snapshot.record,
                metadata=frozen,
                updated_at=_utc_now_iso(),
            )
        )
        subtasks_dir = cache.task_dir(task_id) / "subtasks"
        if subtasks_dir.exists():
            try:
                shutil.rmtree(subtasks_dir)
            except OSError:
                return

    # ------------------------------------------------------------------
    # Public: translation
    # ------------------------------------------------------------------

    def _build_translation_config(
        self,
    ) -> tuple[TranslationConfig, ModelConfig, PromptPreset]:
        settings = self.settings_store.load_all()
        translation = settings.translation
        app = settings.app

        input_dir = _require_directory(translation.input_folder, field="input_folder")
        _require_input_with_supported_files(input_dir, field="input_folder")
        output_dir = _ensure_output_dir(
            translation.output_folder, field="output_folder"
        )
        source_lang = _coerce_language(
            translation.source_language, field="source_language"
        )
        target_lang = _coerce_language(
            translation.target_language, field="target_language"
        )
        model = self._resolve_model_profile(
            app.active_translation_model_id, field="active_translation_model_id"
        )
        # Per-task timeout overrides any value persisted on the model
        # profile — this knob lives in the translation settings UI now.
        model = replace(model, timeout_seconds=float(translation.timeout_seconds))
        preset = self._resolve_prompt_preset(
            app.active_translation_prompt_id, kind=PromptKind.TRANSLATION
        )

        glossary = Glossary.from_records(translation.translation_glossary)
        text_preserve_rules = _coerce_text_preserve_rules(
            translation.text_preserve_rules
        )
        pre_replacements = _coerce_translation_replacements(
            translation.pre_replacements
        )
        post_replacements = _coerce_translation_replacements(
            translation.post_replacements
        )

        config = TranslationConfig(
            input_dir=input_dir,
            output_dir=output_dir,
            source_language=source_lang,
            target_language=target_lang,
            model=model,
            prompt_preset=preset,
            glossary=glossary,
            text_preserve_rules=text_preserve_rules,
            pre_replacements=pre_replacements,
            post_replacements=post_replacements,
            bilingual_enabled=translation.bilingual_enabled,
            bilingual_dedup_when_same=translation.bilingual_dedupe_identical,
            bilingual_subfolder=(
                translation.bilingual_subfolder_name or "bilingual outputs"
            ),
            context_line_count=max(0, int(translation.context_lines)),
            low_confidence_max_retries=max(
                0, int(translation.low_confidence_max_retries)
            ),
        )
        return config, model, preset

    def start_translation(self, request_id: str) -> dict[str, object]:
        config, model, preset = self._build_translation_config()
        input_dir = config.input_dir
        output_dir = config.output_dir
        source_lang = config.source_language
        target_lang = config.target_language

        self._purge_kind_for_start(
            kind="translation", task_kind=TaskKind.TRANSLATION
        )

        task_id = _new_task_id("translation")
        started_at = _utc_now_iso()
        self._seed_placeholder(
            task_id,
            kind=TaskKind.TRANSLATION,
            started_at=started_at,
            metadata={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "source_language": source_lang.value,
                "target_language": target_lang.value,
                "model_id": model.id,
                "prompt_preset_id": preset.id,
                "request_id": request_id,
            },
        )

        cache = self._cache_for_kind("translation")
        running = RunningTask(
            task_id=task_id,
            kind="translation",
            cache=cache,
            created_at=started_at,
        )
        self.registry.add(running)

        def _async_runner() -> None:
            asyncio.run(self._translation_thread(task_id, config, running))

        self._spawn_thread(running, target=_async_runner, task_id=task_id)
        return {"task_id": task_id, "started_at": started_at}

    async def _translation_thread(
        self,
        task_id: str,
        config: TranslationConfig,
        running: RunningTask,
    ) -> None:
        client = self.llm_client_factory()
        cache = self._cache_for_kind("translation")

        def _capture(executor: TaskExecutor) -> None:
            running.set_executor(executor)

        def _finalize(result: TranslationRunResult) -> None:
            payload = _translation_result_payload(result, config=config)
            self._write_result(task_id, payload)

        orchestrator = TranslationOrchestrator(
            cache=cache,
            client=client,
            id_factory=lambda: task_id,
            on_executor_created=_capture,
            on_result_finalized=_finalize,
        )
        await orchestrator.run(config)
        self._maybe_cleanup_cache("translation", task_id)

    # ------------------------------------------------------------------
    # Public: glossary
    # ------------------------------------------------------------------

    def _build_glossary_config(
        self,
    ) -> tuple[GlossaryConfig, ModelConfig, PromptPreset]:
        settings = self.settings_store.load_all()
        glossary = settings.glossary
        app = settings.app

        input_dir = _require_directory(glossary.input_folder, field="input_folder")
        _require_input_with_supported_files(input_dir, field="input_folder")
        output_dir = _ensure_output_dir(
            glossary.output_folder, field="output_folder"
        )
        source_lang = _coerce_language(
            glossary.source_language, field="source_language"
        )
        target_lang = _coerce_language(
            glossary.target_language, field="target_language"
        )
        model = self._resolve_model_profile(
            app.active_glossary_model_id, field="active_glossary_model_id"
        )
        # Per-task timeout overrides any value persisted on the model
        # profile — this knob lives in the glossary settings UI now.
        model = replace(model, timeout_seconds=float(glossary.timeout_seconds))
        preset = self._resolve_prompt_preset(
            app.active_glossary_prompt_id, kind=PromptKind.GLOSSARY
        )

        config = GlossaryConfig(
            input_dir=input_dir,
            output_dir=output_dir,
            source_language=source_lang,
            target_language=target_lang,
            model=model,
            prompt_preset=preset,
            reference_example_limit=max(
                0, int(glossary.reference_examples_per_term)
            ),
            max_term_display_length=max(1, int(glossary.max_term_display_length)),
            min_frequency=max(1, int(glossary.minimum_frequency)),
            chunk_token_limit=max(0, int(glossary.chunk_token_limit)),
            allow_src_eq_dst=bool(glossary.keep_identical_src_dst),
            combine_folder_glossary=bool(glossary.merge_folder_glossary),
            normalize_widths=bool(glossary.normalize_widths),
        )
        return config, model, preset

    def start_glossary(self, request_id: str) -> dict[str, object]:
        config, model, preset = self._build_glossary_config()
        input_dir = config.input_dir
        output_dir = config.output_dir
        source_lang = config.source_language
        target_lang = config.target_language

        self._purge_kind_for_start(
            kind="glossary", task_kind=TaskKind.GLOSSARY
        )

        task_id = _new_task_id("glossary")
        started_at = _utc_now_iso()
        self._seed_placeholder(
            task_id,
            kind=TaskKind.GLOSSARY,
            started_at=started_at,
            metadata={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "source_language": source_lang.value,
                "target_language": target_lang.value,
                "model_id": model.id,
                "prompt_preset_id": preset.id,
                "request_id": request_id,
            },
        )

        cache = self._cache_for_kind("glossary")
        running = RunningTask(
            task_id=task_id,
            kind="glossary",
            cache=cache,
            created_at=started_at,
        )
        self.registry.add(running)

        def _async_runner() -> None:
            asyncio.run(self._glossary_thread(task_id, config, running))

        self._spawn_thread(running, target=_async_runner, task_id=task_id)
        return {"task_id": task_id, "started_at": started_at}

    async def _glossary_thread(
        self,
        task_id: str,
        config: GlossaryConfig,
        running: RunningTask,
    ) -> None:
        client = self.llm_client_factory()
        cache = self._cache_for_kind("glossary")

        def _capture(executor: TaskExecutor) -> None:
            running.set_executor(executor)

        def _finalize(result: GlossaryExtractionResult) -> None:
            payload = _glossary_result_payload(result, config=config)
            self._write_result(task_id, payload)

        orchestrator = GlossaryOrchestrator(
            cache=cache,
            client=client,
            id_factory=lambda: task_id,
            on_executor_created=_capture,
            on_result_finalized=_finalize,
        )
        await orchestrator.run(config)
        self._maybe_cleanup_cache("glossary", task_id)

    # ------------------------------------------------------------------
    # Public: replacement
    # ------------------------------------------------------------------

    def start_replacement(
        self, *, request_id: str, rules: Sequence[ReplacementRule]
    ) -> dict[str, object]:
        settings = self.settings_store.load_all()
        replacement = settings.replacement

        input_dir = _require_directory(
            replacement.input_folder, field="input_folder"
        )
        output_dir = _ensure_output_dir(
            replacement.output_folder, field="output_folder"
        )
        if (
            not replacement.allow_same_folder
            and input_dir.resolve() == output_dir.resolve()
        ):
            raise BridgeError.invalid_argument(
                "input_folder and output_folder must differ unless allow_same_folder is true.",
                field="output_folder",
            )

        if not rules:
            raise BridgeError.invalid_argument(
                "rules cannot be empty.",
                field="rules",
            )

        self._purge_kind_for_start(
            kind="replacement", task_kind=TaskKind.REPLACEMENT
        )

        task_id = _new_task_id("replacement")
        started_at = _utc_now_iso()
        files = scan_input_directory(input_dir)

        cache = self._cache_for_kind("replacement")
        running = RunningTask(
            task_id=task_id,
            kind="replacement",
            cache=cache,
            created_at=started_at,
        )
        self.registry.add(running)

        if not files:
            self._seed_placeholder(
                task_id,
                kind=TaskKind.REPLACEMENT,
                started_at=started_at,
                metadata={
                    "input_dir": str(input_dir),
                    "output_dir": str(output_dir),
                    "request_id": request_id,
                },
            )
            self._mark_status(task_id, TaskStatus.COMPLETED)
            self._write_result(
                task_id,
                {
                    "kind": "replacement",
                    "output_folder": str(output_dir),
                    "output_files": [],
                    "statistics_json_path": None,
                    "total_replacements": 0,
                },
            )
            running.mark_done()
            return {"task_id": task_id, "started_at": started_at}

        record = TaskRecord(
            id=task_id,
            kind=TaskKind.REPLACEMENT,
            status=TaskStatus.PENDING,
            created_at=started_at,
            updated_at=started_at,
            metadata={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "request_id": request_id,
            },
        )
        subtasks = [
            Subtask(
                id=f"file-{index:04d}",
                task_id=task_id,
                request_payload={
                    "source_file": str(doc.path),
                    "format": doc.format.value,
                    "relative_path": doc.relative_path.as_posix(),
                },
            )
            for index, doc in enumerate(files)
        ]
        cache.write_seed(record, subtasks)

        stop_on_first_error = bool(replacement.stop_on_first_error)

        def _runner_target() -> None:
            self._run_replacement_loop(
                task_id=task_id,
                rules=tuple(rules),
                output_dir=output_dir,
                stop_on_first_error=stop_on_first_error,
                running=running,
            )

        self._spawn_thread(running, target=_runner_target, task_id=task_id)
        return {"task_id": task_id, "started_at": started_at}

    def _run_replacement_loop(
        self,
        *,
        task_id: str,
        rules: tuple[ReplacementRule, ...],
        output_dir: Path,
        stop_on_first_error: bool,
        running: RunningTask,
    ) -> None:
        self._mark_status(task_id, TaskStatus.RUNNING)

        cache = self._cache_for_kind("replacement")
        snapshot = cache.load(task_id)
        outputs: list[Path] = []
        total_replacements = 0
        was_stopped = False
        early_break = False

        for subtask in snapshot.subtasks:
            if running.stop_requested:
                was_stopped = True
                break

            running_state = replace(
                subtask,
                status=SubtaskStatus.RUNNING,
                attempt_count=subtask.attempt_count + 1,
                last_error="",
                last_error_at="",
            )
            cache.save_subtask(running_state)

            payload = subtask.request_payload
            source = Path(str(payload.get("source_file", "")))
            fmt = str(payload.get("format", "txt"))
            try:
                if fmt == DocumentFormat.EPUB.value:
                    result = replace_epub_file(source, output_dir, list(rules))
                else:
                    result = replace_txt_file(source, output_dir, list(rules))
                outputs.append(result.output_path)
                total_replacements += result.replacement_count
                completed = replace(
                    running_state,
                    status=SubtaskStatus.COMPLETED,
                    response_content=json.dumps(
                        {
                            "output_path": str(result.output_path),
                            "replacement_count": result.replacement_count,
                            "errors": list(result.errors),
                        },
                        ensure_ascii=False,
                    ),
                )
                cache.save_subtask(completed)
            except Exception as exc:  # noqa: BLE001
                failed = replace(
                    running_state,
                    status=SubtaskStatus.FAILED,
                    last_error=f"{type(exc).__name__}: {exc}",
                    last_error_at=_utc_now_iso(),
                )
                cache.save_subtask(failed)
                if stop_on_first_error:
                    early_break = True
                    break

        statistics = {
            "kind": "replacement",
            "output_folder": str(output_dir),
            "output_files": [str(p) for p in outputs],
            "statistics_json_path": None,
            "total_replacements": total_replacements,
        }
        self._write_result(task_id, statistics)

        latest = cache.load(task_id)
        progress = latest.progress()
        if was_stopped or (early_break and progress.pending > 0):
            final = TaskStatus.STOPPED
        elif progress.failed > 0 and progress.pending == 0 and progress.running == 0:
            final = TaskStatus.FAILED
        elif progress.pending == 0 and progress.running == 0:
            final = TaskStatus.COMPLETED
        else:
            final = TaskStatus.STOPPED
        self._mark_status(task_id, final)
        self._maybe_cleanup_cache("replacement", task_id)

    # ------------------------------------------------------------------
    # Public: lifecycle controls
    # ------------------------------------------------------------------

    def stop_task(self, *, kind: str, task_id: str) -> dict[str, object]:
        running = self.registry.get(task_id)
        if running is None:
            try:
                self._cache_for_task(task_id).load_record(task_id)
            except TaskNotFoundError as exc:
                raise BridgeError.not_found(
                    f"task {task_id!r} not found.",
                    details={"task_id": task_id},
                ) from exc
            raise BridgeError(
                "task.not_running",
                f"task {task_id!r} is not currently running.",
                retryable=False,
                details={"task_id": task_id},
            )
        if running.kind != kind:
            raise BridgeError.invalid_argument(
                f"task {task_id!r} kind {running.kind!r} does not match {kind!r}.",
                field="task_id",
            )
        running.request_stop()
        return self.read_snapshot(kind=kind, task_id=task_id)

    def pause_task(self, *, kind: str, task_id: str) -> dict[str, object]:
        if kind == "replacement":
            raise BridgeError(
                "task.invalid_transition",
                "pause is not supported for replacement (single-pass tool).",
                retryable=False,
                details={"reason": "single_pass"},
            )
        running = self.registry.get(task_id)
        if running is None:
            try:
                self._cache_for_task(task_id).load_record(task_id)
            except TaskNotFoundError as exc:
                raise BridgeError.not_found(
                    f"task {task_id!r} not found.",
                    details={"task_id": task_id},
                ) from exc
            raise BridgeError(
                "task.not_running",
                f"task {task_id!r} is not currently running.",
                retryable=False,
                details={"task_id": task_id},
            )
        if running.kind != kind:
            raise BridgeError.invalid_argument(
                f"task {task_id!r} kind {running.kind!r} does not match {kind!r}.",
                field="task_id",
            )
        running.request_pause()
        return self.read_snapshot(kind=kind, task_id=task_id)

    def continue_task(self, *, kind: str, task_id: str) -> dict[str, object]:
        if kind == "replacement":
            raise BridgeError(
                "task.invalid_transition",
                "continue is not supported for replacement (single-pass tool).",
                retryable=False,
                details={"reason": "single_pass"},
            )
        record_kind = self._kind(kind)
        try:
            snapshot = self._cache_for_task(task_id).load(task_id)
        except TaskNotFoundError as exc:
            raise BridgeError.not_found(
                f"task {task_id!r} not found.",
                details={"task_id": task_id},
            ) from exc
        if snapshot.record.kind is not record_kind:
            raise BridgeError.invalid_argument(
                f"task {task_id!r} kind mismatch.",
                field="task_id",
            )
        if snapshot.record.status not in (
            TaskStatus.STOPPED,
            TaskStatus.PAUSED,
            TaskStatus.FAILED,
        ):
            raise BridgeError(
                "task.invalid_transition",
                f"continue requires status STOPPED, PAUSED, or FAILED; got {snapshot.record.status.value}.",
                retryable=False,
                details={"status": snapshot.record.status.value},
            )
        progress = snapshot.progress()
        if progress.pending == 0 and progress.failed == 0:
            raise BridgeError(
                "task.invalid_transition",
                "task has no pending or failed subtasks; nothing to continue.",
                retryable=False,
                details={"reason": "no_remaining_work"},
            )

        # An in-flight task with the same id (e.g. a previous continue
        # mid-run) blocks a fresh continue. The frontend must stop
        # first; the bridge surfaces a typed conflict.
        existing = self.registry.get(task_id)
        if existing is not None and not existing.is_done:
            raise BridgeError.conflict(
                f"task {task_id!r} is already running.",
                details={"task_id": task_id},
            )

        if kind == "translation":
            return self._continue_translation(task_id)
        return self._continue_glossary(task_id)

    def _continue_translation(self, task_id: str) -> dict[str, object]:
        config, _model, _preset = self._build_translation_config()
        started_at = _utc_now_iso()
        running = RunningTask(
            task_id=task_id,
            kind="translation",
            cache=self._cache_for_kind("translation"),
            created_at=started_at,
        )
        self.registry.add(running)

        def _async_runner() -> None:
            asyncio.run(self._translation_thread(task_id, config, running))

        self._spawn_thread(running, target=_async_runner, task_id=task_id)
        return {"task_id": task_id, "started_at": started_at}

    def _continue_glossary(self, task_id: str) -> dict[str, object]:
        config, _model, _preset = self._build_glossary_config()
        started_at = _utc_now_iso()
        running = RunningTask(
            task_id=task_id,
            kind="glossary",
            cache=self._cache_for_kind("glossary"),
            created_at=started_at,
        )
        self.registry.add(running)

        def _async_runner() -> None:
            asyncio.run(self._glossary_thread(task_id, config, running))

        self._spawn_thread(running, target=_async_runner, task_id=task_id)
        return {"task_id": task_id, "started_at": started_at}

    def probe_continuable(self, *, kind: str) -> dict[str, object]:
        """Return whether a continuable cache exists for ``kind`` under
        the current settings (architecture § 1.3).

        Replacement is single-pass: always returns
        ``continuable=false`` regardless of cache state.
        """

        if kind == "replacement":
            return {
                "continuable": False,
                "task_id": None,
                "status": None,
                "pending": 0,
                "failed": 0,
            }
        record_kind = self._kind(kind)
        settings = self.settings_store.load_all()
        if kind == "translation":
            input_folder = settings.translation.input_folder
            output_folder = settings.translation.output_folder
        else:
            input_folder = settings.glossary.input_folder
            output_folder = settings.glossary.output_folder

        cache = self._cache_for_kind(kind)
        candidates = sorted(
            (r for r in cache.list_tasks() if r.kind is record_kind),
            key=lambda r: r.created_at,
            reverse=True,
        )
        for record in candidates:
            metadata = record.metadata
            if metadata.get("input_dir") != input_folder:
                continue
            if metadata.get("output_dir") != output_folder:
                continue
            try:
                snapshot = cache.load(record.id)
            except (TaskNotFoundError, ValueError, OSError):
                continue
            if snapshot.record.status not in (
                TaskStatus.STOPPED,
                TaskStatus.PAUSED,
                TaskStatus.FAILED,
            ):
                continue
            progress = snapshot.progress()
            if progress.pending + progress.failed <= 0:
                continue
            return {
                "continuable": True,
                "task_id": record.id,
                "status": snapshot.record.status.value,
                "pending": progress.pending,
                "failed": progress.failed,
            }
        return {
            "continuable": False,
            "task_id": None,
            "status": None,
            "pending": 0,
            "failed": 0,
        }

    # ------------------------------------------------------------------
    # Public: read endpoints
    # ------------------------------------------------------------------

    def read_snapshot(self, *, kind: str, task_id: str) -> dict[str, object]:
        record_kind = self._kind(kind)
        cache = self._cache_for_task(task_id)
        try:
            snapshot = cache.load(task_id)
        except TaskNotFoundError as exc:
            raise BridgeError.not_found(
                f"task {task_id!r} not found.",
                details={"task_id": task_id},
            ) from exc
        if snapshot.record.kind is not record_kind:
            raise BridgeError.invalid_argument(
                f"task {task_id!r} kind mismatch.",
                field="task_id",
            )
        snapshot = self._reconcile_zombie(snapshot, cache)
        return {"snapshot": _format_snapshot(snapshot)}

    def list_recent_tasks(
        self, *, kind: str, limit: int | None
    ) -> dict[str, object]:
        record_kind = self._kind(kind)
        cache = self._cache_for_kind(kind)
        records = [r for r in cache.list_tasks() if r.kind is record_kind]
        records.sort(key=lambda r: r.created_at, reverse=True)
        if limit is not None and limit >= 0:
            records = records[:limit]
        return {"tasks": [_format_header(r) for r in records]}

    def list_failed_subtasks(
        self, *, kind: str, task_id: str
    ) -> dict[str, object]:
        record_kind = self._kind(kind)
        try:
            snapshot = self._cache_for_task(task_id).load(task_id)
        except TaskNotFoundError as exc:
            raise BridgeError.not_found(
                f"task {task_id!r} not found.",
                details={"task_id": task_id},
            ) from exc
        if snapshot.record.kind is not record_kind:
            raise BridgeError.invalid_argument(
                f"task {task_id!r} kind mismatch.",
                field="task_id",
            )
        return {"failures": _format_failures(snapshot)}

    def read_artifacts(self, *, kind: str, task_id: str) -> dict[str, object]:
        record_kind = self._kind(kind)
        cache = self._cache_for_task(task_id)
        try:
            record = cache.load_record(task_id)
        except TaskNotFoundError as exc:
            raise BridgeError.not_found(
                f"task {task_id!r} not found.",
                details={"task_id": task_id},
            ) from exc
        if record.kind is not record_kind:
            raise BridgeError.invalid_argument(
                f"task {task_id!r} kind mismatch.",
                field="task_id",
            )
        result = self._read_result(task_id)
        if result is None:
            snapshot = cache.load(task_id)
            return self._partial_result(record=record, snapshot=snapshot)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _kind(kind: str) -> TaskKind:
        record_kind = _KIND_TO_TASKKIND.get(kind)
        if record_kind is None:
            raise BridgeError.invalid_argument(
                f"unsupported task kind: {kind!r}",
                field="kind",
            )
        return record_kind

    def _resolve_model_profile(
        self, profile_id: str | None, *, field: str
    ) -> ModelConfig:
        if not profile_id:
            raise BridgeError.invalid_argument(
                f"{field} is not set; pick a model profile in App Settings.",
                field=field,
            )
        profile = self.profile_store.get(profile_id)
        if profile is None:
            raise BridgeError.invalid_argument(
                f"model profile {profile_id!r} does not exist.",
                field=field,
            )
        if not profile.api_keys:
            raise BridgeError.invalid_argument(
                f"model profile {profile_id!r} has no API key configured.",
                field=field,
                details={"reason": "missing_api_key"},
            )
        return profile

    def _resolve_prompt_preset(
        self, preset_id: str | None, *, kind: PromptKind
    ) -> PromptPreset:
        filename = (
            "prompts.translation.json"
            if kind is PromptKind.TRANSLATION
            else "prompts.glossary.json"
        )
        store = PromptPresetStore(
            path=self.prompts_cache_root / filename, kind=kind
        )
        return store.get_active(preset_id)

    def _purge_kind_for_start(
        self, *, kind: str, task_kind: TaskKind, join_timeout: float = 30.0
    ) -> None:
        """Make room for a fresh ``start`` on the given kind.

        Per architecture § 1.2, ``start`` is destructive: any prior
        in-flight task of this kind is stopped (cooperatively) and any
        prior cache record is deleted. Output files in the user's
        ``output_dir`` are NOT touched — re-running subtasks
        overwrites them naturally. The frontend is responsible for
        showing a confirmation dialog before invoking start when a
        prior task or cache exists; the bridge does not refuse.
        """

        # 1) Cooperative stop on every live thread for this kind.
        live = [r for r in self.registry.list_by_kind(kind) if not r.is_done]
        for running in live:
            running.request_stop()
        for running in live:
            if running.thread is not None:
                running.thread.join(timeout=join_timeout)

        # 2) Delete every cache record of this kind, including the
        #    just-stopped ones and stale completed/failed records.
        cache = self._cache_for_kind(kind)
        for record in cache.list_tasks():
            if record.kind is task_kind:
                try:
                    cache.delete(record.id)
                except TaskNotFoundError:
                    continue

    def _spawn_thread(
        self,
        running: RunningTask,
        *,
        target: Callable[[], None],
        task_id: str,
    ) -> None:
        def _runner() -> None:
            try:
                target()
            except BaseException as exc:  # noqa: BLE001
                running.mark_done(error=exc)
                self._on_task_failure(task_id, exc)
                return
            running.mark_done()

        thread = threading.Thread(
            target=_runner, name=f"task-{task_id}", daemon=True
        )
        running.thread = thread
        thread.start()

    def _seed_placeholder(
        self,
        task_id: str,
        *,
        kind: TaskKind,
        started_at: str,
        metadata: Mapping[str, object],
    ) -> None:
        record = TaskRecord(
            id=task_id,
            kind=kind,
            status=TaskStatus.PENDING,
            created_at=started_at,
            updated_at=started_at,
            metadata=dict(metadata),
        )
        self._cache_for_task(task_id).save_task(record)

    def _mark_status(self, task_id: str, status: TaskStatus) -> None:
        cache = self._cache_for_task(task_id)
        try:
            record = cache.load_record(task_id)
        except TaskNotFoundError:
            return
        if record.status is status:
            return
        cache.save_task(
            record.with_status(status).with_updated_at(_utc_now_iso())
        )

    def _reconcile_zombie(
        self, snapshot: TaskSnapshot, cache: TaskCache
    ) -> TaskSnapshot:
        # After a host-process crash (e.g. macOS WebKit setCursorFromBundle
        # SIGBUS, OOM, kill -9) the runtime never writes a terminal state,
        # so the cache stays at RUNNING/STOPPING/PAUSING while the in-memory
        # executor is gone. Flip it to STOPPED here so the UI surfaces a
        # continuable task instead of a zombie.
        record = snapshot.record
        if record.status not in _ZOMBIE_TASK_STATES:
            return snapshot
        if self.registry.get(record.id) is not None:
            return snapshot
        healed = record.with_status(TaskStatus.STOPPED).with_updated_at(
            _utc_now_iso()
        )
        cache.save_task(healed)
        healed_subtasks: list[Subtask] = []
        for subtask in snapshot.subtasks:
            if subtask.status is SubtaskStatus.RUNNING:
                pending = replace(subtask, status=SubtaskStatus.PENDING)
                cache.save_subtask(pending)
                healed_subtasks.append(pending)
            else:
                healed_subtasks.append(subtask)
        return TaskSnapshot(record=healed, subtasks=tuple(healed_subtasks))

    def _on_task_failure(self, task_id: str, exc: BaseException) -> None:
        cache = self._cache_for_task(task_id)
        try:
            record = cache.load_record(task_id)
        except TaskNotFoundError:
            return
        message = f"{type(exc).__name__}: {exc}"
        metadata = dict(record.metadata)
        metadata["last_error"] = message
        metadata["last_traceback"] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )[-2000:]
        cache.save_task(
            replace(
                record,
                status=TaskStatus.FAILED,
                metadata=metadata,
                updated_at=_utc_now_iso(),
            )
        )

    def _result_path(self, task_id: str) -> Path:
        return self._cache_for_task(task_id).task_dir(task_id) / _RESULT_FILENAME

    def _write_result(
        self, task_id: str, payload: Mapping[str, object]
    ) -> None:
        path = self._result_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _read_result(self, task_id: str) -> dict[str, object] | None:
        path = self._result_path(task_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BridgeError(
                "bridge.io_error",
                f"cannot read artifacts for task {task_id!r}: {exc}",
                retryable=False,
                details={"task_id": task_id, "path": str(path)},
            ) from exc
        except ValueError as exc:
            raise BridgeError(
                "task.artifact_corrupt",
                f"artifact payload for task {task_id!r} is not valid JSON.",
                retryable=False,
                details={"task_id": task_id, "path": str(path)},
            ) from exc
        if isinstance(payload, dict):
            return payload
        raise BridgeError(
            "task.artifact_corrupt",
            f"artifact payload for task {task_id!r} must be a JSON object.",
            retryable=False,
            details={"task_id": task_id, "path": str(path)},
        )

    def _partial_result(
        self, *, record: TaskRecord, snapshot: TaskSnapshot
    ) -> dict[str, object]:
        metadata = record.metadata
        output_dir = Path(str(metadata.get("output_dir", "")))
        if record.kind is TaskKind.TRANSLATION:
            return _partial_translation_payload(snapshot, output_dir=output_dir)
        if record.kind is TaskKind.GLOSSARY:
            input_dir = Path(str(metadata.get("input_dir", "")))
            return _partial_glossary_payload(
                output_dir=output_dir,
                input_folder_name=input_dir.name,
                statistics_dir=self._cache_for_kind("glossary").task_dir(record.id),
            )
        if record.kind is TaskKind.REPLACEMENT:
            return _partial_replacement_payload(snapshot, output_dir=output_dir)
        raise BridgeError.invalid_argument(
            f"unsupported task kind: {record.kind.value!r}",
            field="kind",
        )


def default_llm_client_factory() -> LlmClient:
    """Default factory used in production: a real httpx-backed transport."""

    return LlmClient(transport=HttpxChatTransport())


__all__ = [
    "REPLACED_SUFFIX",
    "TaskService",
    "LlmClientFactory",
    "default_llm_client_factory",
]
