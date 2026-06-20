"""Tests for ``transoria.bridge.handlers.replacement``."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.replacement import register_parsers


@pytest.fixture
def router() -> BridgeRouter:
    router = BridgeRouter()
    register_parsers(router)
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
            "case_sensitive": True,
            "enabled": True,
        },
        {
            "src": "foo",
            "dst": "bar->extra",
            "regex": False,
            "case_sensitive": True,
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


def test_import_rules_decodes_legacy_korean_cp949(router, tmp_path: Path):
    """Bridge import handler must reuse the txt parser's tolerant
    encoding cascade. cp949-encoded rule files are common from users
    migrating older glossaries — refusing them with a UTF-8 decode
    error blocks a legitimate workflow."""

    rules_file = tmp_path / "rules.txt"
    rules_file.write_bytes("권세혁->Logan\n로건->Logan\n".encode("cp949"))

    response = router.call("replacement.import_rules", {"path": str(rules_file)})

    assert response["parse_warnings"] == []
    assert [(rule["src"], rule["dst"]) for rule in response["rules"]] == [
        ("권세혁", "Logan"),
        ("로건", "Logan"),
    ]


def test_import_rules_strips_hash_anchor_markers(router, tmp_path: Path):
    """Some replacement-rule conventions wrap each phrase in ``#`` as
    a context anchor (``src#->#dst``). The marker is not part of the
    actual text — leaving it in would make the rule never match in a
    user's source file (no literal ``#`` to find). The bridge import
    must strip the symmetric markers, mirroring the legacy
    ``load_replacement_rules_txt`` behavior so both code paths agree."""

    rules_file = tmp_path / "rules.txt"
    rules_file.write_text(
        "我没能守护在他身边#->#我没能守护在她身边\n"
        "弟弟妹妹#->#弟弟\n"
        # Asymmetric — no stripping (one-sided # is more likely intentional).
        "权세혁->#Logan\n"
        # No # at all — unchanged.
        "old->new\n",
        encoding="utf-8",
    )

    response = router.call("replacement.import_rules", {"path": str(rules_file)})

    assert response["parse_warnings"] == []
    assert [(rule["src"], rule["dst"]) for rule in response["rules"]] == [
        ("我没能守护在他身边", "我没能守护在她身边"),
        ("弟弟妹妹", "弟弟"),
        ("权세혁", "#Logan"),
        ("old", "new"),
    ]


def test_import_rules_treats_arrow_line_starting_with_hash_as_rule(
    router, tmp_path: Path
):
    """A line that begins with ``#`` AND has no ``->`` is a comment.
    A line that begins with ``#`` AND has ``->`` is a rule — the
    leading ``#`` belongs to the source phrase. This split keeps the
    comment heuristic from silently dropping legitimate phrases that
    happen to start with ``#``."""

    rules_file = tmp_path / "rules.txt"
    rules_file.write_text(
        "# pure comment, must be skipped\n"
        "#leading->fine\n",
        encoding="utf-8",
    )

    response = router.call("replacement.import_rules", {"path": str(rules_file)})

    assert response["parse_warnings"] == []
    # Leading `#` on src is preserved (not the symmetric anchor pattern).
    assert response["rules"] == [
        {
            "src": "#leading",
            "dst": "fine",
            "regex": False,
            "case_sensitive": True,
            "enabled": True,
        }
    ]


def test_import_rules_parses_reeden_red_rules(router, tmp_path: Path):
    rules_file = tmp_path / "rules.red"
    payload = {
        "version": 1,
        "type": "purifyRule",
        "data": [
            {
                "rule": "南根石",
                "target": "男根石",
                "isRegex": False,
                "enabled": True,
            },
            {
                "rule": r"第(\d+)章",
                "target": r"Chapter \1",
                "isRegex": True,
                "enabled": False,
            },
        ],
    }
    rules_file.write_bytes(
        b"RED\x01" + gzip.compress(json.dumps(payload).encode("utf-8"))
    )

    response = router.call("replacement.import_rules", {"path": str(rules_file)})

    assert response["parse_warnings"] == []
    assert response["rules"] == [
        {
            "src": "南根石",
            "dst": "男根石",
            "regex": False,
            "case_sensitive": True,
            "enabled": True,
        },
        {
            "src": r"第(\d+)章",
            "dst": r"Chapter \1",
            "regex": True,
            "case_sensitive": True,
            "enabled": False,
        },
    ]


def test_import_rules_parses_plain_json_red_rules(router, tmp_path: Path):
    rules_file = tmp_path / "rules.red"
    rules_file.write_text(
        json.dumps(
            [
                {"替换原文": "旧词", "替换后": "新词"},
                {"src": "", "dst": "ignored"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = router.call("replacement.import_rules", {"path": str(rules_file)})

    assert [(rule["src"], rule["dst"]) for rule in response["rules"]] == [
        ("旧词", "新词")
    ]
    assert response["parse_warnings"] == [
        {"line_number": 2, "message": "empty source phrase"}
    ]


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


def test_lifecycle_methods_absent_until_register_tasks(router):
    """``register_parsers`` alone leaves lifecycle endpoints unregistered.

    The production router pairs ``register_parsers`` with
    :func:`register_tasks`, so a router that only has the parsers
    surface should respond to lifecycle calls with
    ``bridge.not_found`` rather than a stale unsupported stub.
    """

    with pytest.raises(BridgeError) as caught:
        router.call("replacement.start_task", {"request_id": "rid", "rules": []})

    assert caught.value.code == "bridge.not_found"
