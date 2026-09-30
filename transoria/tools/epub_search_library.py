from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def validate_searches(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("Saved searches must contain at most 100 rules.")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each saved search must be an object.")
        for key, maximum in (("id", 100), ("name", 200), ("query", 2000), ("replacement", 100000)):
            text = item.get(key)
            if not isinstance(text, str) or len(text) > maximum or (key != "replacement" and not text.strip()):
                raise ValueError(f"Invalid saved search {key}.")
        if item["id"] in seen:
            raise ValueError("Saved search IDs must be unique.")
        if item.get("scope") not in {"current", "text", "styles", "all", "selection"}:
            raise ValueError("Invalid saved search scope.")
        if not all(isinstance(item.get(key), bool) for key in ("caseSensitive", "regularExpression")):
            raise ValueError("Saved search options must be boolean.")
        seen.add(item["id"])
        result.append({key: item[key] for key in ("id", "name", "query", "replacement", "scope", "caseSensitive", "regularExpression")})
        if "ignoreMarkup" in item:
            if not isinstance(item["ignoreMarkup"], bool):
                raise ValueError("Ignore markup must be boolean.")
            result[-1]["ignoreMarkup"] = item["ignoreMarkup"]
    return result


class SearchLibrary:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.entries: list[dict[str, object]] = []

    def load(self) -> list[dict[str, object]]:
        if self.path and self.path.exists():
            if self.path.stat().st_size > 12_000_000:
                raise ValueError("Saved search library exceeds the size limit.")
            self.entries = validate_searches(json.loads(self.path.read_text(encoding="utf-8")))
        return self.entries

    def save(self, value: object) -> list[dict[str, object]]:
        entries = validate_searches(value)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".epub-search-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(entries, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        self.entries = entries
        return entries
