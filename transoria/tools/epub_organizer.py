from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping


_EPUB_SUFFIX = ".epub"
_MATCH_THRESHOLD = 50
_INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*]')
_KOREAN_PATTERN = re.compile(r"[가-힣]+")

_TRAILING_MARKERS = (
    r"\s*\d+화$",
    r"\s*제\d+화$",
    r"\s*\d+회$",
    r"\s*제\d+회$",
    r"\s*\d+권$",
    r"\s*제\d+권$",
    r"\s*\d+부$",
    r"\s*제\d+부$",
    r"\s*\d+책$",
    r"\s*제\d+책$",
    r"\s*완결$",
    r"\s*완$",
    r"\s*전체$",
    r"\s*전권$",
    r"\s*외전$",
    r"\s*특별판$",
    r"\s*번외편$",
    r"\s*完結$",
    r"\s*完$",
    r"\s*全集$",
    r"\s*番外$",
    r"\s*特典$",
    r"\s*第\d+话$",
    r"\s*第\d+章$",
    r"\s*第\d+回$",
    r"\s*第\d+卷$",
    r"\s*Vol\.?\s*\d+$",
    r"\s*Volume\s*\d+$",
    r"\s*Book\s*\d+$",
    r"\s*Part\s*\d+$",
    r"\s*\d+$",
    r"\s*-\s*\d+$",
    r"\s*_\s*\d+$",
)

_MID_VOLUME_MARKERS = (
    r"\s*\d+권\s*",
    r"\s*제\d+권\s*",
    r"\s*\d+부\s*",
    r"\s*제\d+부\s*",
    r"\s*\d+책\s*",
    r"\s*제\d+책\s*",
)


@dataclass(frozen=True)
class EpubOrganizeAction:
    id: str
    source_name: str
    target_folder: str
    target_name: str
    operation: str
    score: int
    reason: str
    selected: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_name": self.source_name,
            "target_folder": self.target_folder,
            "target_name": self.target_name,
            "operation": self.operation,
            "score": self.score,
            "reason": self.reason,
            "selected": self.selected,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> EpubOrganizeAction:
        return cls(
            id=str(data.get("id", "")),
            source_name=str(data.get("source_name", "")),
            target_folder=str(data.get("target_folder", "")),
            target_name=str(data.get("target_name", "")),
            operation=str(data.get("operation", "move")),
            score=int(data.get("score", 0)),
            reason=str(data.get("reason", "")),
            selected=bool(data.get("selected", True)),
        )


@dataclass(frozen=True)
class EpubOrganizePlan:
    input_dir: Path
    folders: tuple[str, ...]
    epub_files: tuple[str, ...]
    actions: tuple[EpubOrganizeAction, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "input_dir": str(self.input_dir),
            "folders": list(self.folders),
            "epub_files": list(self.epub_files),
            "actions": [action.to_dict() for action in self.actions],
            "totals": {
                "folders": len(self.folders),
                "epub_files": len(self.epub_files),
                "actions": len(self.actions),
                "create_folder": sum(
                    1 for action in self.actions if action.operation == "create_folder"
                ),
                "move_existing": sum(
                    1 for action in self.actions if action.operation == "move_existing"
                ),
            },
        }


@dataclass(frozen=True)
class EpubOrganizeMoveResult:
    action_id: str
    source_name: str
    target_folder: str
    target_name: str
    source_path: str
    target_path: str
    status: str
    created_folder: bool
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "source_name": self.source_name,
            "target_folder": self.target_folder,
            "target_name": self.target_name,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "status": self.status,
            "created_folder": self.created_folder,
            "error": self.error,
        }


def normalize_epub_title(text: str) -> str:
    name = _strip_epub_suffix(text)
    for pattern in _TRAILING_MARKERS:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)
    name = re.sub(r"[【】\[\]()（）《》「」<>:'\"\s.\-_~!@#$%^&*=+]+", " ", name)
    return " ".join(name.split()).strip()


def extract_korean(text: str) -> str:
    return " ".join(_KOREAN_PATTERN.findall(text))


def create_folder_name(epub_name: str) -> str:
    name = _strip_epub_suffix(epub_name)
    for pattern in _TRAILING_MARKERS:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)
    name = _INVALID_FOLDER_CHARS.sub("", name)
    name = name.strip(" .-_~!@#$%^&*=+[]()【】（）《》「」")
    if not name:
        name = _INVALID_FOLDER_CHARS.sub("", _strip_epub_suffix(epub_name))
    if len(name) > 100:
        name = name[:100] + "..."
    return name or "未分类EPUB"


def create_group_key(epub_name: str) -> str:
    key = create_folder_name(epub_name)
    for pattern in _MID_VOLUME_MARKERS:
        key = re.sub(pattern, " ", key, flags=re.IGNORECASE)
    return " ".join(key.split()).strip()


def scan_epub_organizer(input_dir: Path) -> EpubOrganizePlan:
    base = _require_directory(input_dir)
    folders: list[str] = []
    epub_files: list[str] = []
    for child in sorted(base.iterdir(), key=lambda p: _korean_first_key(p.name)):
        if child.is_dir():
            folders.append(child.name)
        elif child.is_file() and child.suffix.lower() == _EPUB_SUFFIX:
            epub_files.append(child.name)
    return EpubOrganizePlan(
        input_dir=base,
        folders=tuple(folders),
        epub_files=tuple(epub_files),
        actions=tuple(_build_actions(folders, epub_files, base)),
    )


def execute_epub_organize_action(
    input_dir: Path,
    action: EpubOrganizeAction,
    *,
    move_file: Callable[[Path, Path], object] | None = None,
) -> EpubOrganizeMoveResult:
    base = _require_directory(input_dir)
    source = base / action.source_name
    target_folder = base / action.target_folder
    target = target_folder / (action.target_name or action.source_name)
    created_folder = False
    move = move_file or shutil.move
    try:
        source = _direct_child(base, action.source_name)
        target_folder_name = _safe_folder_name(action.target_folder)
        target_folder = _direct_child(base, target_folder_name)
        target_name = _safe_epub_name(action.target_name or action.source_name)
        target = _direct_child(target_folder, target_name)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"source EPUB not found: {source.name}")
        if source.suffix.lower() != _EPUB_SUFFIX:
            raise ValueError(f"source is not an EPUB file: {source.name}")
        if source.resolve().parent != base:
            raise ValueError("source must be a direct child of the input folder")
        if not target_folder.exists():
            target_folder.mkdir(parents=True, exist_ok=True)
            created_folder = True
        if not target_folder.is_dir():
            raise NotADirectoryError(f"target is not a folder: {target_folder.name}")
        target = _unique_destination(target)
        move(source, target)
        return EpubOrganizeMoveResult(
            action_id=action.id,
            source_name=action.source_name,
            target_folder=target_folder.name,
            target_name=target.name,
            source_path=str(source),
            target_path=str(target),
            status="moved",
            created_folder=created_folder,
        )
    except Exception as exc:  # noqa: BLE001
        return EpubOrganizeMoveResult(
            action_id=action.id,
            source_name=action.source_name,
            target_folder=target_folder.name,
            target_name=target.name,
            source_path=str(source),
            target_path=str(target),
            status="failed",
            created_folder=created_folder,
            error=f"{type(exc).__name__}: {exc}",
        )


def build_epub_organize_report(
    *,
    task_id: str,
    input_dir: Path,
    generated_at: str,
    results: Iterable[EpubOrganizeMoveResult],
) -> dict[str, object]:
    rows = [result.to_dict() for result in results]
    moved = sum(1 for row in rows if row["status"] == "moved")
    failed = sum(1 for row in rows if row["status"] == "failed")
    created = sorted(
        {
            str(row["target_folder"])
            for row in rows
            if row["status"] == "moved" and row["created_folder"]
        }
    )
    return {
        "task_id": task_id,
        "generated_at": generated_at,
        "input_dir": str(input_dir),
        "totals": {
            "actions": len(rows),
            "moved": moved,
            "failed": failed,
            "created_folders": len(created),
        },
        "created_folders": created,
        "results": rows,
    }


def _build_actions(
    folders: list[str], epub_files: list[str], input_dir: Path
) -> list[EpubOrganizeAction]:
    actions: list[EpubOrganizeAction] = []
    folder_keys = {folder.casefold() for folder in folders}
    created_by_group: dict[str, str] = {}
    for index, epub in enumerate(epub_files):
        folder, score, reason = _best_folder_match(folders, epub)
        if folder is not None and score >= _MATCH_THRESHOLD:
            target_folder = folder
            operation = "move_existing"
        else:
            group_key = create_group_key(epub)
            target_folder = created_by_group.get(group_key)
            if target_folder is None:
                target_folder = _unique_folder_name(
                    create_folder_name(epub), folder_keys
                )
                created_by_group[group_key] = target_folder
                folder_keys.add(target_folder.casefold())
            score = 0
            reason = "new_folder"
            operation = "create_folder"
        target_name = _planned_target_name(input_dir / target_folder, epub)
        actions.append(
            EpubOrganizeAction(
                id=f"epub-{index:04d}",
                source_name=epub,
                target_folder=target_folder,
                target_name=target_name,
                operation=operation,
                score=score,
                reason=reason,
            )
        )
    return actions


def _best_folder_match(folders: list[str], epub_name: str) -> tuple[str | None, int, str]:
    epub_normalized = normalize_epub_title(epub_name)
    epub_korean = extract_korean(epub_name)
    best_folder: str | None = None
    best_score = 0
    best_reason = ""
    for folder in folders:
        folder_normalized = normalize_epub_title(folder)
        folder_korean = extract_korean(folder)
        score = 0
        reason = ""
        if folder_normalized and folder_normalized == epub_normalized:
            score = 100
            reason = "normalized_exact"
        elif folder_normalized and epub_normalized and folder_normalized in epub_normalized:
            score = 80
            reason = "folder_in_epub"
        elif epub_normalized and folder_normalized and epub_normalized in folder_normalized:
            score = 70
            reason = "epub_in_folder"
        elif folder_korean and epub_korean and folder_korean == epub_korean:
            score = 90
            reason = "korean_exact"
        elif folder_korean and epub_korean and folder_korean in epub_korean:
            score = 75
            reason = "folder_korean_in_epub"
        elif folder_korean and epub_korean and epub_korean in folder_korean:
            score = 65
            reason = "epub_korean_in_folder"
        if score > best_score:
            best_folder = folder
            best_score = score
            best_reason = reason
    return best_folder, best_score, best_reason


def _strip_epub_suffix(name: str) -> str:
    return re.sub(r"\.epub$", "", name, flags=re.IGNORECASE)


def _require_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"input directory does not exist: {path}")
    return resolved


def _direct_child(base: Path, name: str) -> Path:
    if not name or Path(name).name != name:
        raise ValueError(f"unsafe path component: {name!r}")
    child = base / name
    try:
        child.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes input directory: {name!r}") from exc
    return child


def _safe_folder_name(name: str) -> str:
    cleaned = _INVALID_FOLDER_CHARS.sub("", name).strip()
    if not cleaned:
        raise ValueError("target_folder cannot be empty")
    return cleaned


def _safe_epub_name(name: str) -> str:
    if Path(name).name != name:
        raise ValueError(f"unsafe EPUB filename: {name!r}")
    if Path(name).suffix.lower() != _EPUB_SUFFIX:
        raise ValueError(f"target_name must end with .epub: {name!r}")
    return name


def _unique_folder_name(base_name: str, existing_casefold: set[str]) -> str:
    candidate = base_name
    counter = 1
    while candidate.casefold() in existing_casefold:
        candidate = f"{base_name}_{counter}"
        counter += 1
    return candidate


def _planned_target_name(folder: Path, epub_name: str) -> str:
    return _unique_destination(folder / epub_name).name


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _korean_first_key(name: str) -> tuple[int, str]:
    korean = extract_korean(name)
    return (0, korean) if korean else (1, name.casefold())


__all__ = [
    "EpubOrganizeAction",
    "EpubOrganizeMoveResult",
    "EpubOrganizePlan",
    "build_epub_organize_report",
    "create_folder_name",
    "create_group_key",
    "execute_epub_organize_action",
    "extract_korean",
    "normalize_epub_title",
    "scan_epub_organizer",
]
