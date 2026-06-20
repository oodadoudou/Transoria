from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from transoria.workflows.glossary_review.loader import (
    GlossaryReviewInputError,
    discover_review_input_candidates,
    load_review_input,
    normalize_output_filename,
)


def _write_xlsx(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_load_review_input_detects_columns_and_merges_txt(tmp_path: Path) -> None:
    _write_xlsx(
        tmp_path / "terms.xlsx",
        [
            ["原文", "译文", "分类", "出现次数"],
            ["신해범", "申海范", "人物", 3],
        ],
    )
    (tmp_path / "b.txt").write_text("second 신해범", encoding="utf-8")
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")

    loaded = load_review_input(tmp_path, output_filename="final.xlsx")

    assert loaded.glossary.rows[0].src == "신해범"
    assert loaded.glossary.rows[0].dst == "申海范"
    assert loaded.glossary.rows[0].info == "人物"
    assert loaded.glossary.rows[0].frequency == 3
    assert loaded.reference_files == (tmp_path / "a.txt", tmp_path / "b.txt")
    assert loaded.reference_text.startswith("first")


def test_load_review_input_rejects_multiple_xlsx_candidates(tmp_path: Path) -> None:
    _write_xlsx(tmp_path / "a.xlsx", [["src", "dst"], ["a", "甲"]])
    _write_xlsx(tmp_path / "b.xlsx", [["src", "dst"], ["b", "乙"]])
    (tmp_path / "refs.txt").write_text("a b", encoding="utf-8")

    with pytest.raises(GlossaryReviewInputError, match="multiple"):
        load_review_input(tmp_path, output_filename="final.xlsx")


def test_load_review_input_uses_selected_xlsx_and_references(tmp_path: Path) -> None:
    _write_xlsx(tmp_path / "a.xlsx", [["src", "dst"], ["a", "甲"]])
    _write_xlsx(tmp_path / "b.xlsx", [["src", "dst"], ["b", "乙"]])
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second b", encoding="utf-8")

    loaded = load_review_input(
        tmp_path,
        output_filename="final.xlsx",
        selected_xlsx_path=tmp_path / "b.xlsx",
        selected_reference_paths=(tmp_path / "b.txt",),
    )

    assert loaded.glossary.workbook_path == tmp_path / "b.xlsx"
    assert loaded.glossary.rows[0].src == "b"
    assert loaded.reference_files == (tmp_path / "b.txt",)
    assert loaded.reference_text == "second b"


def test_discover_review_input_candidates_lists_visible_files(tmp_path: Path) -> None:
    _write_xlsx(tmp_path / "b.xlsx", [["src", "dst"], ["b", "乙"]])
    _write_xlsx(tmp_path / "a.xlsx", [["src", "dst"], ["a", "甲"]])
    _write_xlsx(tmp_path / "glossary-review-final.xlsx", [["src", "dst"], ["x", "X"]])
    (tmp_path / "b.txt").write_text("second", encoding="utf-8")
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")

    candidates = discover_review_input_candidates(
        tmp_path, output_filename="glossary-review-final.xlsx"
    )

    assert candidates.xlsx_files == (tmp_path / "a.xlsx", tmp_path / "b.xlsx")
    assert candidates.reference_files == (tmp_path / "a.txt", tmp_path / "b.txt")


def test_load_review_input_excludes_final_output_filename(tmp_path: Path) -> None:
    _write_xlsx(tmp_path / "terms.xlsx", [["src", "dst"], ["a", "甲"]])
    _write_xlsx(tmp_path / "glossary-review-final.xlsx", [["src", "dst"], ["x", "X"]])
    (tmp_path / "refs.txt").write_text("a", encoding="utf-8")

    loaded = load_review_input(tmp_path, output_filename="glossary-review-final.xlsx")

    assert loaded.glossary.workbook_path == tmp_path / "terms.xlsx"


def test_normalize_output_filename() -> None:
    assert normalize_output_filename("reviewed") == "reviewed.xlsx"
    with pytest.raises(GlossaryReviewInputError):
        normalize_output_filename("../bad.xlsx")
    with pytest.raises(GlossaryReviewInputError):
        normalize_output_filename("bad.csv")
