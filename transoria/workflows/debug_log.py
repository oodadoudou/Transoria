"""Per-subtask debug log writer used by both workflow runners.

When ``debug_log_dir`` is set on a workflow config, each successful subtask
writes a JSON file capturing the assembled system prompt, user prompt, raw
LLM response, decoded result, and decode issues. Useful when a translation
or glossary candidate looks wrong and the user wants to inspect what
actually went into and came out of the model.

Failures during the log write are swallowed — debug logging must never
prevent a real translation from completing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def write_subtask_debug_log(
    debug_log_dir: Path | None,
    subtask_id: str,
    payload: Mapping[str, object],
) -> Path | None:
    if debug_log_dir is None:
        return None
    safe_id = _FILENAME_SAFE.sub("_", subtask_id) or "subtask"
    path = Path(debug_log_dir) / f"{safe_id}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
            encoding="utf-8",
        )
    except OSError:
        return None
    return path


__all__ = ["write_subtask_debug_log"]
