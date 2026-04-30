"""Translation workflow configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from transoria.domain import Language
from transoria.llm.config import ModelConfig
from transoria.prompts import PromptPreset
from transoria.workflows.fake_name import FakeNameRoster, FakeNameSession
from transoria.workflows.translation.rules import (
    Glossary,
    ReplacementRule,
    TextPreserveRule,
)


BILINGUAL_OUTPUT_FOLDER_EN = "bilingual outputs"
BILINGUAL_OUTPUT_FOLDER_ZH = "双语版本"


@dataclass(frozen=True)
class TranslationConfig:
    input_dir: Path
    output_dir: Path
    source_language: Language
    target_language: Language
    model: ModelConfig
    prompt_preset: PromptPreset

    glossary: Glossary = field(default_factory=Glossary.empty)
    text_preserve_rules: tuple[TextPreserveRule, ...] = ()
    pre_replacements: tuple[ReplacementRule, ...] = ()
    post_replacements: tuple[ReplacementRule, ...] = ()

    bilingual_enabled: bool = False
    bilingual_dedup_when_same: bool = True
    bilingual_subfolder: str = BILINGUAL_OUTPUT_FOLDER_EN

    context_line_count: int = 4
    chunk_size: int = 8
    chunk_token_limit: int = 0
    token_counter: Callable[[str], int] | None = None

    enable_confidence_check: bool = True
    min_length_ratio: float = 0.3
    max_length_ratio: float = 3.0
    max_punctuation_delta: int = 4
    low_confidence_max_retries: int = 3

    stream: bool = False
    debug_log_dir: Path | None = None

    fake_name_roster: FakeNameRoster | FakeNameSession = field(
        default_factory=FakeNameSession
    )

    failed_chunk_split_rounds: int = 3

    # When True, the translation orchestrator buffers each source EPUB's
    # archive bytes in memory at parse time so writeback survives the source
    # file being moved or deleted mid-task. The trade-off is peak memory:
    # archive bytes for every parsed EPUB are held simultaneously until
    # writeback. Default off — typical Korean novels are 1-5 MB and the
    # disk-reread path at writeback is fine. Opt in for unstable input
    # directories or very long batches.
    buffer_epub_archives: bool = False


__all__ = [
    "BILINGUAL_OUTPUT_FOLDER_EN",
    "BILINGUAL_OUTPUT_FOLDER_ZH",
    "TranslationConfig",
]
