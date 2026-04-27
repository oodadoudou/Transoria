"""Two-stage Novel Mode: extract glossary, then translate using its results.

The two pipelines stay independent at the data-model level — Novel Mode is
purely an orchestration helper. Stage 1 (Glossary Extraction) writes the
three artifacts to disk; Novel Mode reads the produced ``<Name>-Glossary.json``
back into a :class:`Glossary`, replaces the translation config's glossary,
and runs Stage 2 (Translation).

If Stage 1 produces no usable glossary entries (every file failed, or the
frequency filter dropped everything), Stage 2 still runs — but with the
caller-provided fallback glossary, not an empty one. That preserves the
explicit "user already has a curated glossary" workflow.

Stage 1 failure (the orchestrator returns ``TaskStatus.FAILED``) does NOT
auto-trigger Stage 2; the caller decides whether to retry.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from transoria.domain import TaskStatus
from transoria.llm.client import LlmClient
from transoria.runtime.cache import TaskCache
from transoria.workflows.glossary import (
    GlossaryConfig,
    GlossaryExtractionResult,
    GlossaryOrchestrator,
)
from transoria.workflows.translation import (
    Glossary,
    TranslationConfig,
    TranslationOrchestrator,
    TranslationRunResult,
)


@dataclass(frozen=True)
class NovelModeConfig:
    glossary: GlossaryConfig
    translation: TranslationConfig
    abort_on_glossary_failure: bool = True


@dataclass(frozen=True)
class NovelModeResult:
    glossary: GlossaryExtractionResult
    translation: TranslationRunResult | None
    used_extracted_glossary: bool = False


@dataclass
class NovelModeOrchestrator:
    cache: TaskCache
    client: LlmClient

    async def run(self, config: NovelModeConfig) -> NovelModeResult:
        glossary_orch = GlossaryOrchestrator(cache=self.cache, client=self.client)
        glossary_result = await glossary_orch.run(config.glossary)

        if (
            config.abort_on_glossary_failure
            and glossary_result.final_status is TaskStatus.FAILED
        ):
            return NovelModeResult(
                glossary=glossary_result, translation=None, used_extracted_glossary=False
            )

        merged_glossary = _load_extracted_glossary(glossary_result)
        translation_glossary = (
            merged_glossary
            if merged_glossary.entries
            else config.translation.glossary
        )
        translation_config = replace(
            config.translation, glossary=translation_glossary
        )

        translation_orch = TranslationOrchestrator(cache=self.cache, client=self.client)
        translation_result = await translation_orch.run(translation_config)

        return NovelModeResult(
            glossary=glossary_result,
            translation=translation_result,
            used_extracted_glossary=bool(merged_glossary.entries),
        )


def _load_extracted_glossary(result: GlossaryExtractionResult) -> Glossary:
    """Merge every per-file glossary JSON the extractor produced into one."""

    records: list[dict[str, object]] = []
    for triple in result.glossary_outputs_per_file:
        json_path = next(
            (path for path in triple if path.suffix == ".json"), None
        )
        if json_path is None or not json_path.exists():
            continue
        merged = Glossary.from_json_file(json_path)
        for entry in merged.entries:
            records.append(
                {
                    "src": entry.src,
                    "dst": entry.dst,
                    "info": entry.info,
                    "regex": entry.regex,
                    "case_sensitive": entry.case_sensitive,
                    "enabled": entry.enabled,
                }
            )
    return Glossary.from_records(records)


__all__ = [
    "NovelModeConfig",
    "NovelModeOrchestrator",
    "NovelModeResult",
]
