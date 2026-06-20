from __future__ import annotations

import json
from pathlib import Path

import pytest

from transoria.workflows.glossary import (
    GlossaryRecord,
    glossary_basename,
    write_glossary_json,
)
from transoria.workflows.translation import Glossary


def test_from_records_round_trips_glossary_extractor_output(tmp_path: Path) -> None:
    records = (
        GlossaryRecord(
            src="신해범", dst="申海范", info="Male Name", frequency=12
        ),
        GlossaryRecord(src="공이", dst="孔二", info="Author", frequency=3),
    )
    json_path = write_glossary_json(records, tmp_path, basename=glossary_basename(Path("Novel.txt")))

    glossary = Glossary.from_json_file(json_path)

    assert {entry.src for entry in glossary.entries} == {"신해범", "공이"}
    matches = glossary.match("신해범 walked into the room.")
    assert matches and matches[0].dst == "申海范"


def test_from_records_skips_entries_without_src_or_dst() -> None:
    records = [
        {"src": "", "dst": "no src"},
        {"src": "no dst", "dst": ""},
        {"src": "ok", "dst": "OK"},
    ]

    glossary = Glossary.from_records(records)

    assert [entry.src for entry in glossary.entries] == ["ok"]


def test_from_records_normalizes_string_boolean_fields() -> None:
    records = [
        {
            "src": "신해범",
            "dst": "申海范",
            "regex": "false",
            "case_sensitive": "True",
            "enabled": "yes",
        }
    ]

    glossary = Glossary.from_records(records)
    entry = glossary.entries[0]

    assert entry.regex is False
    assert entry.case_sensitive is True
    assert entry.enabled is True


def test_from_json_file_rejects_non_array_payload(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON array"):
        Glossary.from_json_file(path)


def test_from_json_file_handles_empty_array(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8")

    glossary = Glossary.from_json_file(path)

    assert glossary.entries == ()
