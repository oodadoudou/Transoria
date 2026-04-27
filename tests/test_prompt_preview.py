"""Pins ``build_prompt`` as the canonical "preview built prompt" path.

Both Translation and Glossary Extraction design docs require a UI-facing
"preview built prompt" action. The frontend should call ``build_prompt`` —
the same function the runners use — so the preview is byte-identical to
what the LLM will see. This test exists so a future refactor can't move the
function or change its contract without us noticing.
"""

from __future__ import annotations

from transoria.prompts import (
    PromptContext,
    PromptKind,
    build_prompt,
    default_preset,
)


def test_translation_preview_uses_target_language_substitution() -> None:
    preset = default_preset(PromptKind.TRANSLATION)

    preview = build_prompt(
        preset,
        PromptContext(source_language="ko", target_language="zh"),
        thinking=False,
    )

    assert "{target_language}" not in preview
    assert "into zh" in preview


def test_translation_preview_with_thinking_true_includes_reasoning_block() -> None:
    preset = default_preset(PromptKind.TRANSLATION)

    preview = build_prompt(
        preset,
        PromptContext(source_language="ko", target_language="zh"),
        thinking=True,
    )

    assert "<why>" in preview


def test_glossary_preview_default_substitutes_target_language() -> None:
    preset = default_preset(PromptKind.GLOSSARY)

    preview = build_prompt(
        preset,
        PromptContext(source_language="ko", target_language="zh"),
        thinking=False,
    )

    assert "{target_language}" not in preview
    assert "zh" in preview
