from __future__ import annotations

import json

from transoria.workflows.debug_log import write_subtask_debug_log


def test_write_subtask_debug_log_uses_compact_json_without_losing_fields(tmp_path):
    path = write_subtask_debug_log(
        tmp_path,
        "chunk:bad id",
        {
            "system_prompt": "s",
            "user_prompt": "u",
            "raw_response": "r",
            "attempts": [{"round": 1, "raw_response": "r"}],
        },
    )

    assert path is not None
    assert path.name == "chunk_bad_id.json"
    raw = path.read_text(encoding="utf-8")
    assert "\n" not in raw
    assert json.loads(raw)["attempts"][0]["raw_response"] == "r"
