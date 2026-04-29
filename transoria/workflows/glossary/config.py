"""Glossary Extraction workflow configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from transoria.domain import Language
from transoria.llm.config import ModelConfig
from transoria.prompts import PromptPreset
from transoria.workflows.fake_name import FakeNameSession


# Per design doc §"Recommended defaults":
# - reference example limit: 20
# - minimum frequency: 1
# - maximum term display length: 32 display-width units (we use codepoints)
DEFAULT_REFERENCE_EXAMPLE_LIMIT = 20
DEFAULT_MAX_TERM_DISPLAY_LENGTH = 32
DEFAULT_MIN_FREQUENCY = 1
DEFAULT_CHUNK_CHAR_LIMIT = 4000

# KG-style generic info values that add noise to novel glossaries.
DEFAULT_INFO_BLACKLIST: tuple[str, ...] = ("其它", "其他", "other", "others")


@dataclass(frozen=True)
class GlossaryConfig:
    input_dir: Path
    output_dir: Path
    source_language: Language
    target_language: Language
    model: ModelConfig
    prompt_preset: PromptPreset

    reference_example_limit: int = DEFAULT_REFERENCE_EXAMPLE_LIMIT
    max_term_display_length: int = DEFAULT_MAX_TERM_DISPLAY_LENGTH
    min_frequency: int = DEFAULT_MIN_FREQUENCY
    chunk_char_limit: int = DEFAULT_CHUNK_CHAR_LIMIT
    chunk_token_limit: int = 0
    token_counter: Callable[[str], int] | None = None
    info_blacklist: tuple[str, ...] = DEFAULT_INFO_BLACKLIST
    allow_src_eq_dst: bool = False
    combine_folder_glossary: bool = False
    normalize_widths: bool = True

    stream: bool = False
    debug_log_dir: Path | None = None
    buffer_epub_archives: bool = False
    fake_name_session: FakeNameSession = field(default_factory=FakeNameSession)
    name_injections: dict[str, str] = field(default_factory=dict)


__all__ = [
    "DEFAULT_INFO_BLACKLIST",
    "DEFAULT_CHUNK_CHAR_LIMIT",
    "DEFAULT_MAX_TERM_DISPLAY_LENGTH",
    "DEFAULT_MIN_FREQUENCY",
    "DEFAULT_REFERENCE_EXAMPLE_LIMIT",
    "GlossaryConfig",
]
