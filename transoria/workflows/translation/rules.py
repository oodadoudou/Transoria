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
from functools import lru_cache
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


@dataclass(frozen=True)
class Glossary:
    """A frozen, ordered collection of glossary entries.

    ``match`` scans entries longest first and masks each match, so a term
    inside a longer matched term is not injected again. Plain entries match by
    substring (case-sensitive or case-insensitive); Hangul terms of one or two
    syllables only match at the start of a word. Regex entries use Python's
    ``re``. Disabled entries are skipped. Results keep the longest-first order.
    """

    entries: tuple[GlossaryEntry, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls) -> "Glossary":
        return cls(entries=())

    def match(self, source_text: str) -> tuple[GlossaryEntry, ...]:
        if not source_text or not self.entries:
            return ()
        matched: list[GlossaryEntry] = []
        masked = pending = source_text
        length = None
        for entry in sorted(self.entries, key=lambda entry: len(entry.src), reverse=True):
            if not entry.enabled or not entry.src:
                continue
            # Only strictly longer terms mask, so same-length variants still match.
            if len(entry.src) != length:
                masked, length = pending, len(entry.src)
            pattern = _entry_pattern(entry.src, entry.regex, entry.case_sensitive)
            if pattern is None or pattern.search(masked) is None:
                continue
            matched.append(entry)
            pending = pattern.sub(lambda found: _MASK * len(found.group(0)), pending)
        return tuple(matched)

    def match_many(self, source_texts: Iterable[str]) -> tuple[GlossaryEntry, ...]:
        seen: dict[GlossaryEntry, GlossaryEntry] = {}
        for text in source_texts:
            for entry in self.match(text):
                seen.setdefault(entry, entry)
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


_MASK = "\u0001"
_SHORT_HANGUL = re.compile(r"[\uAC00-\uD7A3]{1,2}")
# A masked longer term counts as a word, so a particle right after it is not a match.
_HANGUL_BEFORE = r"(?<![\u0001\u1100-\u11FF\u3130-\u318F\uAC00-\uD7A3])"


@lru_cache(maxsize=4096)
def _entry_pattern(src: str, regex: bool, case_sensitive: bool) -> re.Pattern[str] | None:
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        try:
            return re.compile(src, flags)
        except re.error:
            return None
    prefix = _HANGUL_BEFORE if _SHORT_HANGUL.fullmatch(src) else ""
    return re.compile(prefix + re.escape(src), flags)


__all__ = [
    "GlossaryEntry",
    "Glossary",
    "TextPreserveRule",
    "ReplacementRule",
]
