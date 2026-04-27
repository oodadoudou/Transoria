"""Glossary, text-preserve, and replacement rules used by the translation
workflow.

Three rule families share enough structure to live together:

- ``GlossaryEntry``: ``src → dst`` term constraints injected into the prompt.
- ``TextPreserveRule``: regex-described spans that must survive translation
  unchanged (URLs, code identifiers, special tokens). The preprocessor swaps
  matches for sentinel placeholders before the LLM call and restores them
  afterwards.
- ``ReplacementRule``: simple find/replace applied either before sending text
  to the LLM (pre-replacement) or after the response is decoded
  (post-replacement). The same shape is reused by `tools.replacement` for
  Batch Replacement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class GlossaryEntry:
    src: str
    dst: str
    info: str = ""
    regex: bool = False
    case_sensitive: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class TextPreserveRule:
    pattern: str
    note: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class ReplacementRule:
    src: str
    dst: str
    regex: bool = False
    case_sensitive: bool = False
    note: str = ""
    enabled: bool = True


# ---------------------------------------------------------------------------
# Glossary matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Glossary:
    """A frozen, ordered collection of glossary entries.

    ``match`` returns entries whose ``src`` appears anywhere in the given
    source text. Plain entries are matched by substring (case-sensitive or
    case-insensitive). Regex entries use Python's ``re.search``. Disabled
    entries are skipped. Results are sorted by descending ``src`` length so
    longer terms appear first — this is what the prompt expects in order to
    discourage the model from translating sub-fragments differently than the
    parent term.
    """

    entries: tuple[GlossaryEntry, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls) -> "Glossary":
        return cls(entries=())

    def match(self, source_text: str) -> tuple[GlossaryEntry, ...]:
        if not source_text or not self.entries:
            return ()
        matched: list[GlossaryEntry] = []
        for entry in self.entries:
            if not entry.enabled or not entry.src:
                continue
            if _entry_matches(entry, source_text):
                matched.append(entry)
        matched.sort(key=lambda entry: len(entry.src), reverse=True)
        return tuple(matched)

    def match_many(self, source_texts: Iterable[str]) -> tuple[GlossaryEntry, ...]:
        seen: dict[tuple[str, str, str], GlossaryEntry] = {}
        for text in source_texts:
            for entry in self.match(text):
                key = (entry.src, entry.dst, entry.info)
                seen.setdefault(key, entry)
        ordered = sorted(seen.values(), key=lambda entry: len(entry.src), reverse=True)
        return tuple(ordered)

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, object]]) -> "Glossary":
        """Build a Glossary from glossary-extraction-style records.

        Accepts the dict shape emitted by ``write_glossary_json``
        (``src/dst/info/regex/frequency``) plus the translation glossary
        shape (which adds ``case_sensitive`` / ``enabled``). Missing keys
        fall back to the same defaults as :class:`GlossaryEntry`.
        """

        entries: list[GlossaryEntry] = []
        for record in records:
            src = str(record.get("src", "")).strip()
            dst = str(record.get("dst", "")).strip()
            if not src or not dst:
                continue
            entries.append(
                GlossaryEntry(
                    src=src,
                    dst=dst,
                    info=str(record.get("info", "")),
                    regex=_coerce_bool(record.get("regex", False)),
                    case_sensitive=_coerce_bool(record.get("case_sensitive", False)),
                    enabled=_coerce_bool(record.get("enabled", True)),
                )
            )
        return cls(entries=tuple(entries))

    @classmethod
    def from_json_file(cls, path: Path | str) -> "Glossary":
        """Read a ``<Name>-Glossary.json`` file and return a Glossary instance.

        Closes the loop between Glossary Extraction (which writes the file)
        and Translation (which consumes it). The loader normalises any
        boolean fields that may have been hand-edited as strings.
        """

        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError(
                f"Glossary file must contain a JSON array: {path}"
            )
        records: Sequence[Mapping[str, object]] = [
            item for item in payload if isinstance(item, Mapping)
        ]
        return cls.from_records(records)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n", ""}:
            return False
    return bool(value)


def _entry_matches(entry: GlossaryEntry, source_text: str) -> bool:
    if entry.regex:
        flags = 0 if entry.case_sensitive else re.IGNORECASE
        try:
            return re.search(entry.src, source_text, flags=flags) is not None
        except re.error:
            return False
    if entry.case_sensitive:
        return entry.src in source_text
    return entry.src.lower() in source_text.lower()


__all__ = [
    "GlossaryEntry",
    "Glossary",
    "TextPreserveRule",
    "ReplacementRule",
]
