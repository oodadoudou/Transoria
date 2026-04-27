from __future__ import annotations

from transoria.workflows.translation import Glossary, GlossaryEntry


def test_glossary_match_returns_only_terms_present_in_source() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="신해범", dst="申海范", info="Male Name"),
            GlossaryEntry(src="공이", dst="孔二", info="Author"),
            GlossaryEntry(src="흑룡", dst="黑龙", info="Creature"),
        )
    )

    matches = glossary.match("신해범 walked into the room with 공이.")

    assert {entry.src for entry in matches} == {"신해범", "공이"}


def test_glossary_match_orders_results_by_descending_source_length() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="신", dst="申"),
            GlossaryEntry(src="신해범", dst="申海范"),
            GlossaryEntry(src="신해", dst="申海"),
        )
    )

    matches = glossary.match("신해범 신해 신")

    assert [entry.src for entry in matches] == ["신해범", "신해", "신"]


def test_glossary_match_case_insensitive_by_default() -> None:
    glossary = Glossary(entries=(GlossaryEntry(src="John", dst="约翰"),))

    assert len(glossary.match("john said hello")) == 1


def test_glossary_match_case_sensitive_when_flag_set() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="John", dst="约翰", case_sensitive=True),
        )
    )

    assert glossary.match("john said hello") == ()
    assert len(glossary.match("John said hello")) == 1


def test_glossary_regex_entry_matches_pattern() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(src=r"\d+화", dst="<chapter>", regex=True),
        )
    )

    matches = glossary.match("3화 시작")

    assert len(matches) == 1


def test_glossary_invalid_regex_is_skipped_not_raised() -> None:
    glossary = Glossary(
        entries=(GlossaryEntry(src="[invalid", dst="x", regex=True),)
    )

    assert glossary.match("anything") == ()


def test_glossary_disabled_entry_is_skipped() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="신해범", dst="申海范", enabled=False),
            GlossaryEntry(src="공이", dst="孔二"),
        )
    )

    matches = glossary.match("신해범 공이")

    assert [entry.src for entry in matches] == ["공이"]


def test_glossary_match_many_dedupes_across_segments() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(src="신해범", dst="申海范"),
            GlossaryEntry(src="공이", dst="孔二"),
        )
    )

    matches = glossary.match_many(["신해범 walked", "공이 said", "신해범 again"])

    assert {entry.src for entry in matches} == {"신해범", "공이"}
    assert len(matches) == 2


def test_empty_glossary_returns_empty_match() -> None:
    assert Glossary.empty().match("anything") == ()
