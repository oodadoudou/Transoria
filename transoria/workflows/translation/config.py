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

    # Per-request line budget. Larger chunks amortize the fixed per-call
    # boilerplate (system prompt, glossary) over more output lines, so
    # token cost per source line drops. 24 is a balanced default — small
    # enough that one bad response only loses 24 lines (and the
    # ``failed_chunk_split_rounds`` mechanism halves further on failure),
    # large enough to hit ~1/3 the per-line cost of the old chunk_size=8.
    context_line_count: int = 4
    chunk_size: int = 24
    chunk_token_limit: int = 0
    token_counter: Callable[[str], int] | None = None

    enable_confidence_check: bool = True
    # Length-ratio bounds widened for cross-language asymmetry. EN→CJK
    # commonly drops to 0.25-0.30 because Chinese/Japanese pack more
    # meaning per character; CJK→EN can stretch above 3.0 for the
    # opposite reason. Tighter bounds were producing false-positive
    # low-confidence flags on legitimate translations.
    min_length_ratio: float = 0.25
    max_length_ratio: float = 4.0
    # Punctuation delta widened from 4 → 12 after real KO→ZH data
    # showed the average legitimate delta sits at ~8 (Korean uses more
    # `…`/`?!` clusters; Chinese adds quote marks 「」 / fullwidth 。).
    # The old threshold flagged 10% of segments as low-confidence even
    # when the translation was correct, triggering wasted retries.
    # 12 still catches genuine sentence-merge / drop failures (delta
    # 15+) while letting normal punctuation drift through.
    max_punctuation_delta: int = 12
    low_confidence_max_retries: int = 3

    stream: bool = False
    debug_log_dir: Path | None = None

    fake_name_roster: FakeNameRoster | FakeNameSession = field(
        default_factory=FakeNameSession
    )

    failed_chunk_split_rounds: int = 3

    # Extra rounds of orchestrator-level auto-retry after the split
    # loop finishes with leftover FAILED subtasks. Each round waits
    # ``_AUTO_RETRY_DELAY_SECONDS`` (30s) before reset+rerun. 0 keeps
    # the legacy "stop after split" behavior. See orchestrator
    # docstring for the full recovery sequence.
    auto_retry_max_rounds: int = 5

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
