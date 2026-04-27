"""Tests for ``transoria.bridge.handlers.replacement``."""

from __future__ import annotations

from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.replacement import register


@pytest.fixture
def router() -> BridgeRouter:
    router = BridgeRouter()
    register(router)
    return router


def test_import_rules_parses_arrow_lines(router, tmp_path: Path):
    rules_file = tmp_path / "rules.txt"
    rules_file.write_text(
        "# this is a comment\nold->new\nfoo->bar->extra\n\n",
        encoding="utf-8",
    )

    response = router.call(
        "replacement.import_rules", {"path": str(rules_file)}
    )

    assert response["parse_warnings"] == []
    assert response["rules"] == [
        {
            "src": "old",
            "dst": "new",
            "regex": False,
            "case_sensitive": False,
            "enabled": True,
        },
        {
            "src": "foo",
            "dst": "bar->extra",
            "regex": False,
            "case_sensitive": False,
            "enabled": True,
        },
    ]


def test_import_rules_reports_missing_arrow(router, tmp_path: Path):
    rules_file = tmp_path / "broken.txt"
    rules_file.write_text("line without separator\nfine->ok\n", encoding="utf-8")

    response = router.call(
        "replacement.import_rules", {"path": str(rules_file)}
    )

    assert len(response["rules"]) == 1
    assert response["parse_warnings"][0]["line_number"] == 1


def test_import_rules_missing_path_returns_not_found(router, tmp_path: Path):
    with pytest.raises(BridgeError) as caught:
        router.call(
            "replacement.import_rules", {"path": str(tmp_path / "missing.txt")}
        )

    assert caught.value.code == "bridge.not_found"


def test_validate_rules_flags_empty_src(router):
    response = router.call(
        "replacement.validate_rules",
        {"rules": [{"src": "", "dst": "x", "regex": False}]},
    )

    assert response["ok"] is False
    assert response["issues"][0]["code"] == "empty_src"


def test_validate_rules_flags_invalid_regex(router):
    response = router.call(
        "replacement.validate_rules",
        {"rules": [{"src": "(unclosed", "dst": "x", "regex": True}]},
    )

    assert response["issues"][0]["code"] == "regex_error"


def test_validate_rules_flags_duplicate_src(router):
    response = router.call(
        "replacement.validate_rules",
        {
            "rules": [
                {"src": "a", "dst": "1"},
                {"src": "a", "dst": "2"},
            ]
        },
    )

    duplicates = [i for i in response["issues"] if i["code"] == "duplicate_src"]
    assert duplicates and duplicates[0]["rule_index"] == 1


def test_start_task_returns_unsupported(router):
    with pytest.raises(BridgeError) as caught:
        router.call("replacement.start_task", {"request_id": "rid", "rules": []})

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["reason"] == "unsupported"
