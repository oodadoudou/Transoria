from __future__ import annotations

from transoria.workflows.translation import (
    ReplacementRule,
    TextPreserveRule,
    postprocess_segment,
    preprocess_segment,
)


def test_preprocess_strips_leading_and_trailing_whitespace() -> None:
    result = preprocess_segment("    안녕하세요    ")

    assert result.prompt_text == "안녕하세요"
    assert result.leading_whitespace == "    "
    assert result.trailing_whitespace == "    "


def test_preprocess_protects_text_preserve_spans() -> None:
    rules = (TextPreserveRule(pattern=r"\{\{[A-Z_]+\}\}"),)

    result = preprocess_segment("Hello {{NAME}}, welcome!", text_preserve_rules=rules)

    assert "{{NAME}}" not in result.prompt_text
    assert len(result.protection.spans) == 1
    assert result.protection.spans[0] == "{{NAME}}"


def test_postprocess_restores_protected_spans() -> None:
    rules = (TextPreserveRule(pattern=r"\{\{[A-Z_]+\}\}"),)
    pre = preprocess_segment("Hello {{NAME}}!", text_preserve_rules=rules)
    # Simulate the LLM keeping the sentinel verbatim, as desired.
    translated = pre.prompt_text.replace("Hello", "안녕")

    final = postprocess_segment(
        translated,
        protection=pre.protection,
        leading_whitespace=pre.leading_whitespace,
        trailing_whitespace=pre.trailing_whitespace,
    )

    assert final == "안녕 {{NAME}}!"


def test_pre_replacement_runs_after_protection_so_sentinels_are_safe() -> None:
    rules_preserve = (TextPreserveRule(pattern=r"\{\{[A-Z_]+\}\}"),)
    rules_pre = (ReplacementRule(src="hello", dst="hi", case_sensitive=False),)

    result = preprocess_segment(
        "Hello {{NAME}}!",
        text_preserve_rules=rules_preserve,
        pre_replacements=rules_pre,
    )

    assert "hi" in result.prompt_text or "Hi" in result.prompt_text
    assert result.protection.spans[0] == "{{NAME}}"


def test_post_replacement_runs_after_restore() -> None:
    rules_post = (ReplacementRule(src="申海范", dst="申海凡", case_sensitive=True),)

    final = postprocess_segment(
        "申海范 stepped in.",
        protection=preprocess_segment("placeholder").protection,  # empty
        post_replacements=rules_post,
    )

    assert final == "申海凡 stepped in."


def test_regex_replacement_in_postprocess() -> None:
    rules_post = (ReplacementRule(src=r"\.\.\.", dst="…", regex=True),)

    final = postprocess_segment(
        "안녕...",
        protection=preprocess_segment("placeholder").protection,
        post_replacements=rules_post,
    )

    assert final == "안녕…"


def test_invalid_regex_replacement_is_skipped_not_raised() -> None:
    rules_post = (ReplacementRule(src="[invalid", dst="x", regex=True),)

    final = postprocess_segment(
        "test",
        protection=preprocess_segment("placeholder").protection,
        post_replacements=rules_post,
    )

    assert final == "test"


def test_disabled_rules_are_ignored() -> None:
    rules_pre = (
        ReplacementRule(src="Hello", dst="Hi", enabled=False),
    )

    result = preprocess_segment("Hello world", pre_replacements=rules_pre)

    assert result.prompt_text == "Hello world"


def test_empty_input_passes_through_unchanged() -> None:
    assert preprocess_segment("").prompt_text == ""
    assert postprocess_segment(
        "",
        protection=preprocess_segment("").protection,
    ) == ""
