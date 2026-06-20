"""opencc integration test (requires the optional dev dep)."""

from __future__ import annotations

import pytest

from transoria.domain import Language
from transoria.llm.decoders import GlossaryEntry
from transoria.workflows.glossary import normalize_candidates


pytest.importorskip("opencc")


def test_normalize_converts_dst_to_traditional_when_target_is_traditional() -> None:
    # Source dst is in Simplified Chinese; the normalizer must convert it
    # to Traditional when the target is CHINESE_TRADITIONAL.
    raw = (GlossaryEntry(src="网络", dst="网络", info=""),)

    candidates = normalize_candidates(
        raw,
        max_term_display_length=32,
        target_language=Language.CHINESE_TRADITIONAL,
        allow_src_eq_dst=True,
    )

    assert candidates, "candidate must survive normalization"
    # ``网络`` (Simplified) → ``網絡`` (Traditional) via opencc s2t.
    assert candidates[0].dst == "網絡"


def test_normalize_preserves_traditional_dst_when_target_is_simplified() -> None:
    raw = (GlossaryEntry(src="x", dst="網絡", info=""),)

    candidates = normalize_candidates(
        raw,
        max_term_display_length=32,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert candidates[0].dst == "網絡"


def test_normalize_skips_conversion_for_non_chinese_target() -> None:
    raw = (GlossaryEntry(src="x", dst="網絡", info=""),)

    candidates = normalize_candidates(
        raw,
        max_term_display_length=32,
        target_language=Language.ENGLISH,
    )

    assert candidates[0].dst == "網絡"
