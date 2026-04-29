"""Fake-name protection roster used by Translation and Glossary Extraction.

Rare CJK characters and bracketed control codes are masked with safe Latin
placeholders before the prompt is sent to the LLM, then restored on the way
back. This prevents the model from silently transliterating or "smoothing
out" unusual proper nouns.

Two entry points: :class:`FakeNameRoster` for caller-supplied static
mappings, and :class:`FakeNameSession` for per-task automatic masking with
serializable state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping


DEFAULT_FAKE_NAMES: tuple[str, ...] = (
    "蓝霁云",
    "檀秋萦",
    "墨临川",
    "泠鸢晚",
    "云螭遥",
    "邝溟幽",
    "颛鹤唳",
    "玄璆夜",
    "砚秋辞",
    "聆音澈",
    "雪渟寒",
    "萤照晚",
    "青霭浮",
    "绛霄临",
    "墨漪澜",
    "霜序遥",
    "霁川流",
    "檀烟渺",
    "玄螭隐",
    "青冥远",
    "風祭宵",
    "月代雫",
    "雨宮静",
    "星影律",
    "霧島朔",
    "時雨遥",
    "雪村茜",
    "花垣葵",
    "水瀬碧",
    "空木凪",
)

_BRACKET_CODE_RE = re.compile(r"\\n{1,2}\[\d+\]", re.IGNORECASE)


@dataclass(frozen=True)
class FakeNameRoster:
    """Caller-supplied character→placeholder map."""

    mapping: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "FakeNameRoster":
        return cls(mapping={})

    def is_empty(self) -> bool:
        return not self.mapping

    def apply(self, text: str) -> str:
        if not self.mapping or not text:
            return text
        out = text
        for source_char, placeholder in self.mapping.items():
            if not source_char:
                continue
            out = out.replace(source_char, placeholder)
        return out

    def restore(self, text: str) -> str:
        if not self.mapping or not text:
            return text
        out = text
        # Iterate over the longest placeholders first so a placeholder that
        # is a substring of another can't accidentally swallow the prefix.
        for source_char, placeholder in sorted(
            self.mapping.items(), key=lambda item: -len(item[1])
        ):
            if not placeholder:
                continue
            out = out.replace(placeholder, source_char)
        return out


@dataclass
class FakeNameSession:
    """Per-task fake-name mapper with automatic token detection."""

    mapping: dict[str, str] = field(default_factory=dict)
    default_names: tuple[str, ...] = DEFAULT_FAKE_NAMES
    detect_bracket_codes: bool = True
    detect_rare_cjk: bool = True

    def is_empty(self) -> bool:
        return not self.mapping

    def apply(self, text: str) -> str:
        if not text:
            return text
        for token in self._detect_tokens(text):
            self._ensure_mapping(token)
        out = text
        for source, placeholder in sorted(
            self.mapping.items(), key=lambda item: -len(item[0])
        ):
            out = out.replace(source, placeholder)
        return out

    def restore(self, text: str) -> tuple[str, bool]:
        if not text:
            return text, False
        out = text
        for source, placeholder in sorted(
            self.mapping.items(), key=lambda item: -len(item[1])
        ):
            out = out.replace(placeholder, source)
        return out, out != text

    def to_dict(self) -> dict[str, object]:
        return {
            "mapping": dict(self.mapping),
            "default_names": list(self.default_names),
            "detect_bracket_codes": self.detect_bracket_codes,
            "detect_rare_cjk": self.detect_rare_cjk,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "FakeNameSession":
        raw_mapping = data.get("mapping", {})
        mapping = (
            {str(key): str(value) for key, value in raw_mapping.items()}
            if isinstance(raw_mapping, Mapping)
            else {}
        )
        raw_names = data.get("default_names", DEFAULT_FAKE_NAMES)
        names = (
            tuple(str(name) for name in raw_names)
            if isinstance(raw_names, list)
            else DEFAULT_FAKE_NAMES
        )
        return cls(
            mapping=mapping,
            default_names=names,
            detect_bracket_codes=bool(data.get("detect_bracket_codes", True)),
            detect_rare_cjk=bool(data.get("detect_rare_cjk", True)),
        )

    def _detect_tokens(self, text: str) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        if self.detect_bracket_codes:
            for match in _BRACKET_CODE_RE.finditer(text):
                seen.setdefault(match.group(0), None)
        if self.detect_rare_cjk:
            for char in text:
                if _is_rare_cjk(char):
                    seen.setdefault(char, None)
        return tuple(seen.keys())

    def _ensure_mapping(self, token: str) -> None:
        if token in self.mapping:
            return
        used = set(self.mapping.values())
        for name in self.default_names:
            if name not in used:
                self.mapping[token] = name
                return
        self.mapping[token] = token


def _is_rare_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x9F00 <= codepoint <= 0x9FFF
        or 0x20000 <= codepoint <= 0x2EBEF
    )


def restore_fake_name_text(helper: object, text: str) -> str:
    restored = helper.restore(text)  # type: ignore[attr-defined]
    if isinstance(restored, tuple):
        return str(restored[0])
    return str(restored)


__all__ = [
    "DEFAULT_FAKE_NAMES",
    "FakeNameRoster",
    "FakeNameSession",
    "restore_fake_name_text",
]
