from __future__ import annotations

from dataclasses import replace

from transoria.settings import default_settings
from transoria.settings.migrations import merge_glossary_stages


def _settings(*, glossary_lang=("en", "zh"), review_lang=("kr", "zh"), glossary_bg="", review_bg="背景", review_folder=""):
    base = default_settings()
    return base.with_module(
        "glossary", replace(base.glossary, source_language=glossary_lang[0], target_language=glossary_lang[1], novel_background=glossary_bg)
    ).with_module(
        "glossary_review",
        replace(base.glossary_review, source_language=review_lang[0], target_language=review_lang[1], novel_background=review_bg, input_folder=review_folder),
    )


def test_latest_review_task_decides_shared_languages_and_background_is_copied():
    merged = merge_glossary_stages(_settings(), {"glossary": {"source_language": "en"}}, "glossary_review")
    assert merged is not None
    assert (merged.glossary.source_language, merged.glossary.target_language) == ("kr", "zh")
    assert merged.glossary.novel_background == "背景"
    assert merged.glossary.review_after_extraction is True
    assert merged.glossary_review == _settings().glossary_review


def test_latest_extraction_task_keeps_extraction_languages_and_own_background():
    merged = merge_glossary_stages(_settings(glossary_bg="提取背景"), {"glossary": {}}, "glossary")
    assert merged is not None
    assert merged.glossary.source_language == "en"
    assert merged.glossary.novel_background == "提取背景"


def test_without_tasks_the_configured_stage_decides():
    review_only = merge_glossary_stages(_settings(review_folder="/books"), {"glossary_review": {}}, None)
    assert review_only is not None and review_only.glossary.source_language == "kr"
    neither = merge_glossary_stages(_settings(), {"glossary": {}}, None)
    assert neither is not None and neither.glossary.source_language == "en"


def test_runs_once_and_skips_fresh_installs():
    assert merge_glossary_stages(_settings(), {"glossary": {"review_after_extraction": False}}, "glossary_review") is None
    assert merge_glossary_stages(_settings(), {"app": {}}, "glossary_review") is None
