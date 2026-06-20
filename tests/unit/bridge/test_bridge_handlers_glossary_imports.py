"""Tests for ``transoria.bridge.handlers.glossary_imports``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.glossary_imports import register


@pytest.fixture
def router(tmp_path: Path) -> BridgeRouter:
    router = BridgeRouter()
    register(router, cache_root=tmp_path)
    return router


def test_import_rules_decodes_legacy_korean_cp949_json(
    router: BridgeRouter, tmp_path: Path
) -> None:
    """A glossary JSON saved by an older tool in cp949 must still
    import — the previous two-try utf-8 / utf-8-sig path raised
    UnicodeDecodeError on anything else."""

    glossary_file = tmp_path / "legacy.json"
    payload = json.dumps(
        [
            {"src": "권세혁", "dst": "Logan", "info": "男性角色"},
            {"src": "로건", "dst": "Logan", "info": "男性角色"},
        ],
        ensure_ascii=False,
    )
    glossary_file.write_bytes(payload.encode("cp949"))

    response = router.call(
        "glossary.import_rules", {"path": str(glossary_file)}
    )

    assert [(entry["src"], entry["dst"]) for entry in response["entries"]] == [
        ("권세혁", "Logan"),
        ("로건", "Logan"),
    ]


def test_import_rules_drops_rows_with_empty_src_or_dst(
    router: BridgeRouter, tmp_path: Path
) -> None:
    """A row with one side empty is meaningless (matches nothing or
    replaces with empty). Earlier versions kept rows where either
    field was non-empty; tighten so both src AND dst must be non-empty."""

    glossary_file = tmp_path / "partial.json"
    payload = json.dumps(
        [
            {"src": "good", "dst": "GOOD", "info": "valid"},
            {"src": "", "dst": "no-src", "info": "invalid"},
            {"src": "no-dst", "dst": "", "info": "invalid"},
            {"src": "  ", "dst": "  ", "info": "all-whitespace"},
            {"src": "another", "dst": "ANOTHER", "info": "valid"},
        ],
        ensure_ascii=False,
    )
    glossary_file.write_bytes(payload.encode("utf-8"))

    response = router.call(
        "glossary.import_rules", {"path": str(glossary_file)}
    )

    assert [(entry["src"], entry["dst"]) for entry in response["entries"]] == [
        ("good", "GOOD"),
        ("another", "ANOTHER"),
    ]


def test_import_rules_still_accepts_utf8_with_bom(
    router: BridgeRouter, tmp_path: Path
) -> None:
    """UTF-8 with BOM (common from Excel/Notepad exports) must keep
    working — the cascade's first-stage utf-8-sig branch handles it."""

    glossary_file = tmp_path / "bom.json"
    payload = json.dumps(
        [{"src": "미아", "dst": "米亚", "info": "男性角色"}],
        ensure_ascii=False,
    )
    glossary_file.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))

    response = router.call(
        "glossary.import_rules", {"path": str(glossary_file)}
    )

    assert [(entry["src"], entry["dst"]) for entry in response["entries"]] == [
        ("미아", "米亚"),
    ]
