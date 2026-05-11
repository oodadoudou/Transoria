"""File-backed task cache.

Layout under the cache root::

    <root>/<task_id>/task.json
    <root>/<task_id>/subtasks/<subtask_id>.json

Each write is atomic (temp file + ``os.replace``) so a crash mid-write cannot
corrupt either the task header or any individual subtask record. The cache
never overwrites the whole task at once — workflows persist subtasks one at a
time as they complete, which is what makes resume cheap.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from transoria.runtime.subtask import Subtask
from transoria.runtime.task_record import TaskRecord, TaskSnapshot


_TASK_FILENAME = "task.json"
_SUBTASKS_DIRNAME = "subtasks"

# Restrict task/subtask ids to safe filename characters so we never need to
# percent-encode or worry about path traversal. Workflows generate ids; tests
# pass them in directly.
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class TaskNotFoundError(KeyError):
    """Raised when a task id has no record in the cache root."""


@dataclass(frozen=True)
class TaskCache:
    """Persistence boundary for the task runtime.

    The cache only knows how to read/write JSON files; it does not enforce
    state-machine rules. The executor decides when to call :meth:`save_task`
    or :meth:`save_subtask`.
    """

    root: Path

    def task_dir(self, task_id: str) -> Path:
        _validate_id(task_id, kind="task")
        return self.root / task_id

    def subtask_path(self, task_id: str, subtask_id: str) -> Path:
        _validate_id(subtask_id, kind="subtask")
        return self.task_dir(task_id) / _SUBTASKS_DIRNAME / f"{subtask_id}.json"

    def save_task(self, record: TaskRecord) -> None:
        target = self.task_dir(record.id) / _TASK_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, record.to_json())

    def save_subtask(self, subtask: Subtask) -> None:
        target = self.subtask_path(subtask.task_id, subtask.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, subtask.to_json())

    def load(self, task_id: str) -> TaskSnapshot:
        record = self.load_record(task_id)
        return TaskSnapshot(record=record, subtasks=self.load_subtasks(task_id))

    def load_record(self, task_id: str) -> TaskRecord:
        path = self.task_dir(task_id) / _TASK_FILENAME
        if not path.exists():
            raise TaskNotFoundError(task_id)
        return TaskRecord.from_dict(_read_json_mapping(path))

    def load_subtasks(self, task_id: str) -> tuple[Subtask, ...]:
        directory = self.task_dir(task_id) / _SUBTASKS_DIRNAME
        if not directory.exists():
            return ()
        subtasks = [
            Subtask.from_dict(_read_json_mapping(child))
            for child in sorted(directory.iterdir())
            if child.is_file() and child.suffix == ".json"
        ]
        return tuple(subtasks)

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        if not self.root.exists():
            return ()
        records: list[TaskRecord] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            task_file = child / _TASK_FILENAME
            if not task_file.exists():
                continue
            try:
                records.append(
                    TaskRecord.from_dict(_read_json_mapping(task_file))
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return tuple(records)

    def has_task(self, task_id: str) -> bool:
        """Return True iff a task record exists for ``task_id``.

        Cheaper than :meth:`load_record`: only checks for the task
        directory and ``task.json`` presence; does not parse the
        record or its subtasks.
        """

        directory = self.task_dir(task_id)
        return (directory / "task.json").exists()

    def delete(self, task_id: str) -> None:
        directory = self.task_dir(task_id)
        if not directory.exists():
            raise TaskNotFoundError(task_id)
        shutil.rmtree(directory)

    def write_seed(self, record: TaskRecord, subtasks: Iterable[Subtask]) -> None:
        """Persist a fresh task and its initial subtasks atomically.

        Subtask files are written first (in a fresh ``subtasks/`` directory),
        and only after every subtask is on disk is the ``task.json`` header
        committed. A crash mid-loop leaves no orphan task header — the next
        run sees only the partial subtask files but no record, so
        :meth:`load_record` raises :class:`TaskNotFoundError` and the caller
        knows to start fresh.
        """

        materialized = list(subtasks)
        # Write subtasks before the task header so that a crash mid-loop
        # leaves an "incomplete" tree that ``load_record`` rejects rather
        # than a header pointing at missing children.
        for subtask in materialized:
            self.save_subtask(subtask)
        self.save_task(record)


def _validate_id(value: str, *, kind: str) -> None:
    if not value or not _SAFE_ID_PATTERN.match(value):
        raise ValueError(
            f"Invalid {kind} id: {value!r} (allowed: A-Z a-z 0-9 . _ -)"
        )


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _read_json_mapping(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        if exc.msg != "Extra data":
            raise
        payload, end = json.JSONDecoder().raw_decode(raw)
        if raw[end:].strip() and not isinstance(payload, dict):
            raise
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


__all__ = ["TaskCache", "TaskNotFoundError"]
