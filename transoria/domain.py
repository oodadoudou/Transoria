from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
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
    RUSSIAN = "ru"
    ARABIC = "ar"
    GERMAN = "de"
    FRENCH = "fr"
    POLISH = "pl"
    SPANISH = "es"
    ITALIAN = "it"
    PORTUGUESE = "pt"
    HUNGARIAN = "hu"
    TURKISH = "tr"
    THAI = "th"
    INDONESIAN = "id"
    VIETNAMESE = "vi"


_LANGUAGE_PROMPT_LABELS: dict[Language, str] = {
    Language.KOREAN: "Korean (한국어)",
    Language.CHINESE_SIMPLIFIED: "Simplified Chinese (简体中文)",
    Language.CHINESE_TRADITIONAL: "Traditional Chinese (繁體中文)",
    Language.ENGLISH: "English",
    Language.JAPANESE: "Japanese (日本語)",
    Language.RUSSIAN: "Russian",
    Language.ARABIC: "Arabic",
    Language.GERMAN: "German",
    Language.FRENCH: "French",
    Language.POLISH: "Polish",
    Language.SPANISH: "Spanish",
    Language.ITALIAN: "Italian",
    Language.PORTUGUESE: "Portuguese",
    Language.HUNGARIAN: "Hungarian",
    Language.TURKISH: "Turkish",
    Language.THAI: "Thai",
    Language.INDONESIAN: "Indonesian",
    Language.VIETNAMESE: "Vietnamese",
}


def language_prompt_label(language: Language) -> str:
    return _LANGUAGE_PROMPT_LABELS.get(language, language.value)


def normalize_target_script(text: str, target_language: Language) -> str:
    converter = _target_script_converter(target_language)
    return converter(text) if converter is not None else text


@lru_cache(maxsize=4)
def _target_script_converter(target_language: Language):
    if target_language is Language.CHINESE_TRADITIONAL:
        config = "s2t"
    else:
        return None
    from opencc import OpenCC

    return OpenCC(config).convert


class TaskStatus(str, Enum):
    """Task state machine.

    Transient states ``STOPPING`` / ``PAUSING`` are surfaced on the
    snapshot so the UI can render "Stopping…" / "Pausing…" while
    in-flight subtasks settle. ``PAUSED`` is a continuable state;
    ``STOPPED`` is also continuable. ``COMPLETED`` and ``FAILED``
    are terminal.
    """

    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    PAUSING = "pausing"
    PAUSED = "paused"
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
    GLOSSARY_REVIEW = "glossary_review"
    REPLACEMENT = "replacement"
    EPUB_ORGANIZE = "epub_organize"


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
