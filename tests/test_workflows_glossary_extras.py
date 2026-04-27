from __future__ import annotations

from pathlib import Path

from transoria.workflows.glossary import (
    Candidate,
    GlossaryRecord,
    build_glossary_chunks,
    count_frequencies_and_references,
    write_glossary_decode_issues,
)
from transoria.workflows.glossary.combine import combine_glossary_records


# ---------------------------------------------------------------------------
# C1: decode-issues file
# ---------------------------------------------------------------------------


def test_decode_issues_file_skipped_when_no_issues(tmp_path: Path) -> None:
    path = write_glossary_decode_issues((), tmp_path, basename="Sample")
    assert path is None


def test_decode_issues_file_writes_one_block_per_issue(tmp_path: Path) -> None:
    issues = (
        {"line": "garbage that cannot parse", "reason": "not JSON"},
        {"line": '{"src":"","dst":"x"}', "reason": "missing or empty 'src'"},
    )

    path = write_glossary_decode_issues(issues, tmp_path, basename="Sample")

    assert path is not None
    assert path.name == "Sample-Glossary-decode-issues.txt"
    body = path.read_text(encoding="utf-8")
    assert "garbage that cannot parse" in body
    assert "missing or empty 'src'" in body
    assert "\n\n" in body  # blocks separated by blank lines


# ---------------------------------------------------------------------------
# C4: regex frequency scan
# ---------------------------------------------------------------------------


def test_regex_candidate_uses_re_finditer_for_frequency() -> None:
    candidates = (
        Candidate(src=r"\d+화", dst="<chapter>", regex=True),
    )
    segments = (
        "1화 시작",
        "Plain line",
        "12화 다음 권",
        "한 줄 더",
    )

    records = count_frequencies_and_references(
        candidates, segments, reference_example_limit=10, min_frequency=1
    )

    assert len(records) == 1
    assert records[0].frequency == 2
    assert records[0].regex is True


def test_regex_candidate_with_invalid_pattern_is_skipped() -> None:
    candidates = (Candidate(src="[invalid", dst="x", regex=True),)

    records = count_frequencies_and_references(
        candidates,
        ("any text",),
        reference_example_limit=5,
        min_frequency=1,
    )

    assert records == ()


# ---------------------------------------------------------------------------
# C5: token-bounded chunking
# ---------------------------------------------------------------------------


def test_chunker_uses_token_counter_when_supplied() -> None:
    # A token counter that pretends each character is two tokens.
    def fake_count(text: str) -> int:
        return len(text) * 2

    segments = (
        "alpha",  # 5 chars × 2 = 10 tokens
        "bravo",  # 10 tokens
        "charlie",  # 14 tokens
    )

    chunks = build_glossary_chunks(
        {Path("/a.txt"): segments},
        chunk_char_limit=999,  # ignored when token mode is active
        chunk_token_limit=15,
        token_counter=fake_count,
    )

    # First chunk holds "alpha" (10) — adding "bravo" (10 + 1 join = 11)
    # exceeds the 15-token budget, so it splits.
    assert len(chunks) >= 2
    assert chunks[0].text == "alpha"


def test_chunker_falls_back_to_chars_when_no_token_counter() -> None:
    chunks = build_glossary_chunks(
        {Path("/a.txt"): ("hello", "world")},
        chunk_char_limit=200,
    )

    assert chunks[0].text == "hello\nworld"


# ---------------------------------------------------------------------------
# C6: combined folder-level glossary
# ---------------------------------------------------------------------------


def _record(src: str, dst: str, freq: int, refs: tuple[str, ...] = ()) -> GlossaryRecord:
    return GlossaryRecord(src=src, dst=dst, info="", frequency=freq, references=refs)


def test_combine_sums_frequency_and_dedupes_references() -> None:
    file_a = (
        _record("신해범", "申海范", freq=8, refs=("신해범 walks.",)),
        _record("공이", "孔二", freq=3, refs=("공이 said.",)),
    )
    file_b = (
        _record("신해범", "申海范", freq=12, refs=("신해범 again.",)),
        _record("흑룡", "黑龙", freq=2, refs=("흑룡 watched.",)),
    )

    combined = combine_glossary_records(
        [file_a, file_b], reference_example_limit=5
    )

    by_src = {record.src: record for record in combined}
    assert by_src["신해범"].frequency == 20
    assert "신해범 walks." in by_src["신해범"].references
    assert "신해범 again." in by_src["신해범"].references
    assert by_src["공이"].frequency == 3
    assert by_src["흑룡"].frequency == 2


def test_combine_picks_majority_dst() -> None:
    file_a = (_record("신해범", "申海范", 5),)
    file_b = (_record("신해범", "申海凡", 5),)
    file_c = (_record("신해범", "申海范", 5),)

    combined = combine_glossary_records(
        [file_a, file_b, file_c], reference_example_limit=5
    )

    assert combined[0].dst == "申海范"  # 2 votes vs 1
