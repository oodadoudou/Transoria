"""Candidate and final-record types used by the glossary extraction workflow.

`Candidate` is the working type during normalization: it carries the
mid-pipeline state (normalized src/dst/info, occurrence count from voting).
`GlossaryRecord` is the final emitted shape that lands in the output XLSX/
JSON/references TXT, with the verified frequency count and the collected
reference lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Candidate:
    """A glossary entry produced by the LLM and run through normalization.

    ``votes`` is the number of distinct chunk-level emissions that resolved
    to this normalized ``src``. The frequency count in the final record is
    measured separately by scanning the source text — that's what catches
    the "term mentioned 200 times in chapter 1" pattern that vote count
    cannot see.

    ``regex`` flags candidates whose ``src`` should be treated as a regular
    expression by the frequency scanner (and downstream by Translation
    glossary matching). The default is ``False``; the LLM-emitted candidates
    we currently support are all literals.
    """

    src: str
    dst: str
    info: str = ""
    votes: int = 1
    regex: bool = False


@dataclass(frozen=True)
class GlossaryRecord:
    """Final glossary entry emitted to disk."""

    src: str
    dst: str
    info: str = ""
    regex: bool = False
    frequency: int = 0
    references: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "src": self.src,
            "dst": self.dst,
            "info": self.info,
            "regex": self.regex,
            "frequency": self.frequency,
        }


__all__ = ["Candidate", "GlossaryRecord"]
