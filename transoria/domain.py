from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DocumentFormat(str, Enum):
    EPUB = "epub"
    TXT = "txt"


class Language(str, Enum):
    KOREAN = "kr"
    CHINESE_SIMPLIFIED = "zh"
    CHINESE_TRADITIONAL = "zh-Hant"
    ENGLISH = "en"
    JAPANESE = "ja"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskKind(str, Enum):
    TRANSLATION = "translation"
    GLOSSARY = "glossary"
    REPLACEMENT = "replacement"


@dataclass(frozen=True)
class DocumentFile:
    path: Path
    relative_path: Path
    format: DocumentFormat

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path.as_posix(),
            "relative_path": self.relative_path.as_posix(),
            "format": self.format.value,
        }


def translated_filename(
    source_path: Path,
    target_language: Language,
    *,
    source_language: Language | None = None,
    bilingual: bool = False,
) -> str:
    stem = source_path.stem
    suffix = source_path.suffix
    language_tag = target_language.value

    if bilingual:
        if source_language is None:
            raise ValueError("source_language is required for bilingual filenames")
        language_tag = f"{language_tag}-{source_language.value}"

    return f"{stem}-{language_tag}{suffix}"

