"""One-time upgrades of stored settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from transoria.settings.defaults import AllSettings


def merge_glossary_stages(
    settings: AllSettings, raw: Mapping[str, object], latest_stage: str | None
) -> AllSettings | None:
    """Fold the review stage's language and novel background into the shared glossary settings.

    Runs once: stored glossary settings written by this version carry
    ``review_after_extraction``. ``latest_stage`` is the kind of the most
    recent extraction or review task, which decides whose languages win.
    Returns None when there is nothing to upgrade.
    """

    raw_glossary = raw.get("glossary")
    raw_glossary = raw_glossary if isinstance(raw_glossary, Mapping) else {}
    if "review_after_extraction" in raw_glossary:
        return None
    if "glossary" not in raw and "glossary_review" not in raw:
        return None
    glossary, review = settings.glossary, settings.glossary_review
    if latest_stage is None:
        # Without task history, the stage the user actually pointed at a folder decides.
        latest_stage = "glossary_review" if review.input_folder and not glossary.input_folder else "glossary"
    source = review if latest_stage == "glossary_review" else glossary
    merged = replace(
        glossary,
        source_language=source.source_language,
        target_language=source.target_language,
        novel_background=glossary.novel_background or review.novel_background,
        review_after_extraction=True,
    )
    return settings.with_module("glossary", merged)


__all__ = ["merge_glossary_stages"]
