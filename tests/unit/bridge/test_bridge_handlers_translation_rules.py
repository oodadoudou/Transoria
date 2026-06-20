"""Tests for ``transoria.bridge.handlers.translation_rules``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.translation_rules import register


@pytest.fixture
def router() -> BridgeRouter:
    router = BridgeRouter()
    register(router)
    return router


# -- import: replacement (LG schema) ----------------------------------------


def test_import_pre_replacement_from_lg_schema_json(
    router: BridgeRouter, tmp_path: Path
) -> None:
    rules_file = tmp_path / "lg.json"
    rules_file.write_text(
        json.dumps(
            [
                {
                    "src": "로건",
                    "dst": "Logan",
                    "regex": False,
                    "case_sensitive": False,
                    "info": "男性角色",
                },
                {"src": "", "dst": "skip-me-empty-src"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = router.call(
        "rules.import_rules",
        {"kind": "pre_replacement", "path": str(rules_file)},
    )

    assert response["kind"] == "pre_replacement"
    assert response["rules"] == [
        {
            "src": "로건",
            "dst": "Logan",
            "regex": False,
            "case_sensitive": False,
            "note": "男性角色",
            "enabled": True,
        }
    ]


def test_import_post_replacement_from_xlsx(
    router: BridgeRouter, tmp_path: Path
) -> None:
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook  # type: ignore[import-not-found]

    path = tmp_path / "post.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(("src", "dst", "info", "regex", "case_sensitive"))
    sheet.append(("Mr.", "先生", "honorific", False, True))
    workbook.save(path)

    response = router.call(
        "rules.import_rules",
        {"kind": "post_replacement", "path": str(path)},
    )

    assert response["rules"] == [
        {
            "src": "Mr.",
            "dst": "先生",
            "regex": False,
            "case_sensitive": True,
            "note": "honorific",
            "enabled": True,
        }
    ]


# -- import: text_preserve --------------------------------------------------


def test_import_text_preserve_native_schema(
    router: BridgeRouter, tmp_path: Path
) -> None:
    path = tmp_path / "preserve.json"
    path.write_text(
        json.dumps(
            [
                {"pattern": r"\{\{[^}]+\}\}", "note": "占位符"},
                {"pattern": "", "note": "skip-empty"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = router.call(
        "rules.import_rules",
        {"kind": "text_preserve", "path": str(path)},
    )

    assert response["rules"] == [
        {"pattern": r"\{\{[^}]+\}\}", "note": "占位符", "enabled": True}
    ]


def test_import_text_preserve_accepts_lg_schema_via_src_alias(
    router: BridgeRouter, tmp_path: Path
) -> None:
    """The LG export format uses ``src`` / ``info``. Importing such a
    file as text-preserve maps src→pattern and info→note so the user
    doesn't need to reformat the file by hand."""

    path = tmp_path / "lg-as-preserve.json"
    path.write_text(
        json.dumps(
            [
                {
                    "src": "Mr.",
                    "dst": "先生",
                    "info": "honorific",
                    "regex": False,
                    "case_sensitive": False,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = router.call(
        "rules.import_rules",
        {"kind": "text_preserve", "path": str(path)},
    )

    assert response["rules"] == [
        {"pattern": "Mr.", "note": "honorific", "enabled": True}
    ]


# -- import: validation -----------------------------------------------------


def test_import_rejects_unknown_kind(
    router: BridgeRouter, tmp_path: Path
) -> None:
    with pytest.raises(BridgeError) as exc:
        router.call(
            "rules.import_rules",
            {"kind": "ranger", "path": str(tmp_path / "x.json")},
        )
    assert exc.value.payload.code == "bridge.invalid_argument"


def test_import_rejects_missing_file(router: BridgeRouter, tmp_path: Path) -> None:
    with pytest.raises(BridgeError) as exc:
        router.call(
            "rules.import_rules",
            {"kind": "text_preserve", "path": str(tmp_path / "missing.json")},
        )
    assert exc.value.payload.code == "bridge.not_found"


def test_import_rejects_unknown_suffix(
    router: BridgeRouter, tmp_path: Path
) -> None:
    path = tmp_path / "rules.txt"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(BridgeError) as exc:
        router.call(
            "rules.import_rules",
            {"kind": "pre_replacement", "path": str(path)},
        )
    assert exc.value.payload.code == "bridge.invalid_argument"


def test_import_rejects_non_array_json(
    router: BridgeRouter, tmp_path: Path
) -> None:
    path = tmp_path / "obj.json"
    path.write_text(json.dumps({"not": "an array"}), encoding="utf-8")
    with pytest.raises(BridgeError) as exc:
        router.call(
            "rules.import_rules",
            {"kind": "pre_replacement", "path": str(path)},
        )
    assert exc.value.payload.code == "bridge.invalid_argument"


def test_import_rejects_malformed_json(
    router: BridgeRouter, tmp_path: Path
) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(BridgeError) as exc:
        router.call(
            "rules.import_rules",
            {"kind": "pre_replacement", "path": str(path)},
        )
    assert exc.value.payload.code == "bridge.io_error"


# -- export -----------------------------------------------------------------


def test_export_replacement_persists_note_as_info(
    router: BridgeRouter, tmp_path: Path
) -> None:
    """In-memory rules use ``note`` (consistent with the rest of the
    UI), but the on-disk JSON uses ``info`` so an exported file imports
    cleanly into third-party tooling that keys on ``info``."""

    out = tmp_path / "out.json"
    response = router.call(
        "rules.export_rules",
        {
            "kind": "pre_replacement",
            "path": str(out),
            "rules": [
                {
                    "src": "로건",
                    "dst": "Logan",
                    "regex": False,
                    "case_sensitive": False,
                    "note": "男性角色",
                    "enabled": True,
                }
            ],
        },
    )

    assert response["count"] == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload == [
        {
            "src": "로건",
            "dst": "Logan",
            "info": "男性角色",
            "regex": False,
            "case_sensitive": False,
            "enabled": True,
        }
    ]


def test_export_text_preserve_uses_native_schema(
    router: BridgeRouter, tmp_path: Path
) -> None:
    out = tmp_path / "preserve.json"
    router.call(
        "rules.export_rules",
        {
            "kind": "text_preserve",
            "path": str(out),
            "rules": [
                {"pattern": r"\{\{[^}]+\}\}", "note": "占位符", "enabled": True}
            ],
        },
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload == [
        {"pattern": r"\{\{[^}]+\}\}", "note": "占位符", "enabled": True}
    ]


def test_export_xlsx_replacement_round_trips(
    router: BridgeRouter, tmp_path: Path
) -> None:
    pytest.importorskip("openpyxl")
    out = tmp_path / "rt.xlsx"
    payload = [
        {
            "src": "Mr.",
            "dst": "先生",
            "regex": False,
            "case_sensitive": True,
            "note": "honorific",
            "enabled": True,
        }
    ]
    router.call(
        "rules.export_rules",
        {"kind": "post_replacement", "path": str(out), "rules": payload},
    )
    response = router.call(
        "rules.import_rules",
        {"kind": "post_replacement", "path": str(out)},
    )
    assert response["rules"] == payload


def test_export_defaults_to_json_when_suffix_missing(
    router: BridgeRouter, tmp_path: Path
) -> None:
    out = tmp_path / "no-suffix"
    response = router.call(
        "rules.export_rules",
        {"kind": "text_preserve", "path": str(out), "rules": []},
    )
    assert response["path"].endswith(".json")
    assert (tmp_path / "no-suffix.json").exists()


def test_export_rejects_unknown_suffix(
    router: BridgeRouter, tmp_path: Path
) -> None:
    out = tmp_path / "rules.txt"
    with pytest.raises(BridgeError) as exc:
        router.call(
            "rules.export_rules",
            {"kind": "text_preserve", "path": str(out), "rules": []},
        )
    assert exc.value.payload.code == "bridge.invalid_argument"


def test_export_rejects_non_list_rules(
    router: BridgeRouter, tmp_path: Path
) -> None:
    out = tmp_path / "x.json"
    with pytest.raises(BridgeError) as exc:
        router.call(
            "rules.export_rules",
            {"kind": "text_preserve", "path": str(out), "rules": "not-a-list"},
        )
    assert exc.value.payload.code == "bridge.invalid_argument"
