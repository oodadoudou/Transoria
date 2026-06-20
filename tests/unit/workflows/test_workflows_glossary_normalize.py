from __future__ import annotations

from transoria.domain import Language
from transoria.llm.decoders import GlossaryEntry
from transoria.workflows.glossary import normalize_candidates


def _entry(src: str, dst: str, info: str = "") -> GlossaryEntry:
    return GlossaryEntry(src=src, dst=dst, info=info)


def test_normalize_drops_empty_terms() -> None:
    candidates = normalize_candidates(
        [_entry("", "申"), _entry("신", ""), _entry("신해범", "申海范")],
        max_term_display_length=32,
    )

    assert [c.src for c in candidates] == ["신해범"]


def test_normalize_drops_src_eq_dst_unless_explicitly_allowed() -> None:
    candidates = normalize_candidates(
        [_entry("Tokyo", "Tokyo")], max_term_display_length=32
    )

    assert candidates == ()

    candidates = normalize_candidates(
        [_entry("Tokyo", "Tokyo")],
        max_term_display_length=32,
        allow_src_eq_dst=True,
    )

    assert len(candidates) == 1


def test_normalize_drops_terms_above_max_length() -> None:
    long_src = "가" * 50

    candidates = normalize_candidates(
        [_entry(long_src, "long"), _entry("ok", "OK")],
        max_term_display_length=32,
    )

    assert [c.src for c in candidates] == ["ok"]


def test_normalize_filters_blacklisted_info_values() -> None:
    candidates = normalize_candidates(
        [
            _entry("신해범", "申海范", info="Male Name"),
            _entry("이름", "名字", info="Other"),
        ],
        max_term_display_length=32,
        info_blacklist=("Other",),
    )

    assert [c.src for c in candidates] == ["신해범"]


def test_normalize_filters_default_generic_info_values() -> None:
    candidates = normalize_candidates(
        [
            _entry("신해범", "申海范", info="角色"),
            _entry("기타", "其他", info="others"),
            _entry("범용", "通用", info="其他"),
        ],
        max_term_display_length=32,
    )

    assert [c.src for c in candidates] == ["신해범"]


def test_normalize_drops_entries_marked_as_rejected_by_prompt() -> None:
    candidates = normalize_candidates(
        [
            _entry("새", "鸟", info="通用生物（已过滤）"),
            _entry("생각", "想法", info="抽象概念（已排除）"),
            _entry("추술사", "咒术师", info="称号/职业"),
        ],
        max_term_display_length=32,
        source_language=Language.KOREAN,
    )

    assert [c.src for c in candidates] == ["추술사"]


def test_normalize_keeps_prompt_defined_categories_without_semantic_filtering() -> None:
    candidates = normalize_candidates(
        [
            _entry("새", "鸟", info="通用生物"),
            _entry("생각", "想法", info="抽象概念"),
            _entry("추술사", "咒术师", info="称号/职业"),
        ],
        max_term_display_length=32,
        source_language=Language.KOREAN,
    )

    assert {c.src for c in candidates} == {"새", "생각", "추술사"}


def test_normalize_drops_terms_without_source_language_script() -> None:
    candidates = normalize_candidates(
        [
            _entry("KakaoTalk", "KakaoTalk", info="软件"),
            _entry("경해수", "景海秀", info="男性角色"),
        ],
        max_term_display_length=32,
        source_language=Language.KOREAN,
        allow_src_eq_dst=True,
    )

    assert [c.src for c in candidates] == ["경해수"]


def test_normalize_dedupes_and_vote_merges_dst_and_info() -> None:
    candidates = normalize_candidates(
        [
            _entry("신해범", "申海范", info="Male Name"),
            _entry("신해범", "申海范", info="Male Name"),
            _entry("신해범", "Shin Hae-bum", info="Character"),
        ],
        max_term_display_length=32,
    )

    assert len(candidates) == 1
    canonical = candidates[0]
    assert canonical.src == "신해범"
    assert canonical.dst == "申海范"  # 2 votes vs 1
    assert canonical.info == "Male Name"
    assert canonical.votes == 3


def test_normalize_splits_compound_terms_when_piece_counts_match() -> None:
    candidates = normalize_candidates(
        [_entry("신해범 / 공이", "申海范 / 孔二")], max_term_display_length=32
    )

    sources = sorted(c.src for c in candidates)
    targets = {c.src: c.dst for c in candidates}
    assert sources == ["공이", "신해범"]
    assert targets["신해범"] == "申海范"
    assert targets["공이"] == "孔二"


def test_normalize_splits_punctuation_separated_terms_when_piece_counts_match() -> None:
    candidates = normalize_candidates(
        [_entry("신해범, 공이", "申海范, 孔二")], max_term_display_length=32
    )

    targets = {c.src: c.dst for c in candidates}
    assert targets == {"신해범": "申海范", "공이": "孔二"}


def test_normalize_does_not_split_when_piece_counts_mismatch() -> None:
    candidates = normalize_candidates(
        [_entry("신해범 / 공이", "申海范")], max_term_display_length=32
    )

    assert [c.src for c in candidates] == ["신해범 / 공이"]


def test_normalize_groups_case_insensitively() -> None:
    candidates = normalize_candidates(
        [_entry("John", "约翰"), _entry("john", "约翰")],
        max_term_display_length=32,
    )

    assert len(candidates) == 1


def test_normalize_collapses_internal_whitespace() -> None:
    candidates = normalize_candidates(
        [_entry("신해범   walks", "申海范   走")], max_term_display_length=32
    )

    assert candidates[0].src == "신해범 walks"
    assert candidates[0].dst == "申海范 走"


def test_normalize_orders_by_descending_votes() -> None:
    candidates = normalize_candidates(
        [
            _entry("a", "A"),
            _entry("b", "B"),
            _entry("b", "B"),
            _entry("c", "C"),
            _entry("c", "C"),
            _entry("c", "C"),
        ],
        max_term_display_length=32,
    )

    assert [c.src for c in candidates] == ["c", "b", "a"]


def test_normalize_folds_full_width_to_half_width_by_default() -> None:
    candidates = normalize_candidates(
        [_entry("ＡＢＣ", "甲乙丙")], max_term_display_length=32
    )

    assert [c.src for c in candidates] == ["ABC"]


def test_normalize_widths_can_be_disabled() -> None:
    candidates = normalize_candidates(
        [_entry("ＡＢＣ", "甲乙丙")],
        max_term_display_length=32,
        normalize_widths=False,
    )

    assert [c.src for c in candidates] == ["ＡＢＣ"]


def test_normalize_strips_boundary_punctuation() -> None:
    candidates = normalize_candidates(
        [
            _entry("李四,", "Lee Si"),
            _entry("「玛丽」", "Mary"),
            _entry("…帝王—", "Emperor"),
        ],
        max_term_display_length=32,
    )

    srcs = {c.src for c in candidates}
    assert srcs == {"李四", "帝王", "玛丽"}


def test_normalize_preserves_internal_apostrophe_and_hyphen() -> None:
    candidates = normalize_candidates(
        [_entry("O'Brien", "奥布莱恩"), _entry("Spider-Man", "蜘蛛侠")],
        max_term_display_length=32,
    )

    srcs = {c.src for c in candidates}
    assert srcs == {"O'Brien", "Spider-Man"}
