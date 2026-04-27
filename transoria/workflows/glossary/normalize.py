"""Candidate normalization pipeline for glossary extraction.

Implements the 10-step pipeline from
``docs/glossary-extraction-module-design.md`` §"Candidate Normalization".

Step 2 (fake-name restore) is currently a no-op — see
``transoria.workflows.fake_name`` for the protection scaffolding used at the
prompt boundary. Step 3 (Chinese form conversion) lives here and uses
``opencc`` lazily; when the optional dependency is missing the step is a
no-op so users without it still get a usable glossary.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from transoria.domain import Language
from transoria.llm.decoders import GlossaryEntry
from transoria.workflows.glossary.candidate import Candidate


_MULTI_SPACE = re.compile(r"\s+")
_SPLIT_DELIMITERS = re.compile(r"\s*[/／、,，;；]\s*")
_DEFAULT_INFO_BLACKLIST = ("其它", "其他", "other", "others")


def normalize_candidates(
    raw_entries: Iterable[GlossaryEntry],
    *,
    max_term_display_length: int,
    info_blacklist: Iterable[str] = _DEFAULT_INFO_BLACKLIST,
    allow_src_eq_dst: bool = False,
    target_language: Language | None = None,
) -> tuple[Candidate, ...]:
    """Run the full normalization pipeline over the raw model outputs.

    Returns the canonical, deduplicated ``Candidate`` list, ordered by
    descending vote count then by ``src`` for stable output.
    """

    blacklist = {value.casefold() for value in info_blacklist if value}
    cn_converter = _build_cn_converter(target_language)
    expanded: list[GlossaryEntry] = []
    for entry in raw_entries:
        expanded.extend(_split_compound_term(entry))

    # Steps 1, 5–8: trim, drop empty / src==dst / over-long / blacklisted.
    cleaned: list[GlossaryEntry] = []
    for entry in expanded:
        src = _collapse_whitespace(entry.src)
        dst = _collapse_whitespace(entry.dst)
        info = entry.info.strip()
        if not src or not dst:
            continue
        if cn_converter is not None:
            dst = cn_converter(dst)
        if not allow_src_eq_dst and src == dst:
            continue
        if len(src) > max_term_display_length:
            continue
        if info and info.casefold() in blacklist:
            continue
        cleaned.append(GlossaryEntry(src=src, dst=dst, info=info))

    # Steps 9, 10: deduplicate by normalized src and vote-merge dst/info.
    grouped: dict[str, list[GlossaryEntry]] = defaultdict(list)
    for entry in cleaned:
        grouped[_group_key(entry.src)].append(entry)

    candidates: list[Candidate] = []
    for group in grouped.values():
        canonical_src = _vote(item.src for item in group)
        canonical_dst = _vote(item.dst for item in group)
        canonical_info = _vote(item.info for item in group)
        candidates.append(
            Candidate(
                src=canonical_src,
                dst=canonical_dst,
                info=canonical_info,
                votes=len(group),
            )
        )

    candidates.sort(key=lambda candidate: (-candidate.votes, candidate.src))
    return tuple(candidates)


def _split_compound_term(entry: GlossaryEntry) -> list[GlossaryEntry]:
    """Split ``a / b -> x / y`` into ``[(a→x), (b→y)]`` when piece counts match.

    If the source and target produce different numbers of pieces, the entry
    is left intact — splitting blindly would corrupt the mapping.
    """

    src_pieces = [piece for piece in _SPLIT_DELIMITERS.split(entry.src) if piece]
    dst_pieces = [piece for piece in _SPLIT_DELIMITERS.split(entry.dst) if piece]
    if len(src_pieces) <= 1 or len(src_pieces) != len(dst_pieces):
        return [entry]
    return [
        GlossaryEntry(src=src_piece, dst=dst_piece, info=entry.info)
        for src_piece, dst_piece in zip(src_pieces, dst_pieces)
    ]


def _collapse_whitespace(text: str) -> str:
    return _MULTI_SPACE.sub(" ", text or "").strip()


def _group_key(src: str) -> str:
    return _collapse_whitespace(src).casefold()


def _vote(values: Iterable[str]) -> str:
    """Return the most frequent value, with deterministic tie-breaking.

    Ties are broken by lexicographic order so the same input always yields
    the same canonical pick — important for reproducible outputs.
    """

    counter = Counter(values)
    counter.pop("", None)
    if not counter:
        return ""
    most_count = max(counter.values())
    candidates = sorted(value for value, count in counter.items() if count == most_count)
    return candidates[0]


def _build_cn_converter(target_language: Language | None):
    """Return an opencc converter callable, or ``None`` to skip the step.

    When ``opencc`` (the optional dependency) is not installed we return
    ``None`` and silently leave dst unchanged. Tests that rely on real
    conversion install ``opencc-python-reimplemented`` separately; production
    can do the same when the user picks Traditional Chinese as a target.
    """

    if target_language is None:
        return None
    if target_language not in (Language.CHINESE_SIMPLIFIED, Language.CHINESE_TRADITIONAL):
        return None
    try:
        from opencc import OpenCC  # type: ignore[import-not-found]
    except ImportError:
        return None
    config = "t2s" if target_language is Language.CHINESE_SIMPLIFIED else "s2t"
    converter = OpenCC(config)
    return converter.convert


__all__ = ["normalize_candidates"]
