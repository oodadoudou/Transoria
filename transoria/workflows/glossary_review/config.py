"""Glossary review workflow configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transoria.llm.config import ModelConfig
from transoria.prompts import PromptPreset

DEFAULT_OUTPUT_FILENAME = "glossary-review-final.xlsx"


@dataclass(frozen=True)
class GlossaryReviewConfig:
    input_dir: Path
    selected_xlsx_path: Path | None
    selected_reference_paths: tuple[Path, ...]
    output_filename: str
    novel_background: str
    review_rounds: int
    batch_size: int
    # Same transport retry budget as request_retry_attempts in other modules;
    # the legacy field name is kept for persisted settings compatibility.
    retry_attempts: int
    model: ModelConfig
    prompt_preset: PromptPreset
    stream: bool = True
    debug_log_dir: Path | None = None


__all__ = ["DEFAULT_OUTPUT_FILENAME", "GlossaryReviewConfig"]
