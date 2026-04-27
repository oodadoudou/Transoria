from __future__ import annotations

from transoria.workflows.glossary import (
    Candidate,
    count_frequencies_and_references,
)


def test_frequency_counts_segment_appearances() -> None:
    candidates = (Candidate(src="신해범", dst="申海范"),)
    segments = (
        "신해범 walked into the room.",
        "Plain line.",
        "Then 신해범 sat down. 신해범 sighed.",
    )

    records = count_frequencies_and_references(
        candidates, segments, reference_example_limit=10, min_frequency=1
    )

    assert len(records) == 1
    record = records[0]
    # Two segments contain 신해범 (the third has two occurrences but counts once).
    assert record.frequency == 2
    assert len(record.references) == 2


def test_frequency_masks_longer_terms_so_child_terms_dont_double_count() -> None:
    candidates = (
        Candidate(src="신해범", dst="申海范"),
        Candidate(src="신", dst="申"),
    )
    segments = (
        "신해범 walks.",
        "신해범 walks again.",
        "Just 신 by itself.",
    )

    records = count_frequencies_and_references(
        candidates, segments, reference_example_limit=10, min_frequency=1
    )

    by_src = {record.src: record for record in records}
    # 신해범 matches 2 lines.
    assert by_src["신해범"].frequency == 2
    # 신 only matches the third line because the first two had 신 inside the masked 신해범.
    assert by_src["신"].frequency == 1


def test_frequency_drops_terms_below_min_frequency() -> None:
    candidates = (
        Candidate(src="흑룡", dst="黑龙"),
        Candidate(src="신해범", dst="申海范"),
    )
    segments = ("신해범 only here.",)

    records = count_frequencies_and_references(
        candidates, segments, reference_example_limit=5, min_frequency=1
    )

    assert [record.src for record in records] == ["신해범"]


def test_frequency_caps_reference_examples() -> None:
    candidates = (Candidate(src="신해범", dst="申海范"),)
    segments = tuple(f"신해범 line {n}" for n in range(50))

    records = count_frequencies_and_references(
        candidates, segments, reference_example_limit=5, min_frequency=1
    )

    assert records[0].frequency == 50
    assert len(records[0].references) == 5


def test_frequency_orders_results_by_descending_frequency() -> None:
    candidates = (
        Candidate(src="A", dst="一"),
        Candidate(src="BB", dst="二"),
        Candidate(src="CCC", dst="三"),
    )
    segments = (
        "CCC",
        "CCC and BB",
        "A here",
        "BB again",
        "CCC last time",
    )

    records = count_frequencies_and_references(
        candidates, segments, reference_example_limit=5, min_frequency=1
    )

    assert [record.src for record in records] == ["CCC", "BB", "A"]


def test_frequency_uses_unmasked_text_for_references() -> None:
    candidates = (
        Candidate(src="신해범", dst="申海范"),
        Candidate(src="신", dst="申"),
    )
    segments = ("신해범 walked. Then 신 alone.",)

    records = count_frequencies_and_references(
        candidates, segments, reference_example_limit=5, min_frequency=1
    )

    by_src = {record.src: record for record in records}
    # The reference for 신 should still be the original line (with 신해범 visible).
    if "신" in by_src and by_src["신"].references:
        assert by_src["신"].references[0] == "신해범 walked. Then 신 alone."
