from __future__ import annotations

import html
import mimetypes
import re
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lxml import etree


_TXT_SUFFIX = ".txt"
_EPUB_SUFFIX = ".epub"
_RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources" / "epub_styles"
_STYLE_GROUPS = {"basic": "通用兼容样式", "enhanced": "增强样式"}
_VISIBLE_STYLE_KEYS = {
    "basic": (
        "classic",
        "clean",
        "eyecare",
        "modern",
        "minimal",
        "literary",
        "compact",
        "spacious",
        "double_line",
        "sans_clean",
        "framed",
        "sidebar",
        "structure_lines",
        "reader_modern",
    ),
    "enhanced": ("soft_structure",),
}
_STYLE_TEMPLATE = """/* transoria-epub-style: v1 */
body {
  margin: 0 5%;
  font-family: serif;
  line-height: 1.8;
}

h1 {
  margin: 2em 0 1.5em;
  text-align: center;
  font-size: 1.6em;
}

h2 {
  margin: 1.8em 0 1.2em;
  text-align: center;
  font-size: 1.35em;
}

h3 {
  margin: 1.5em 0 1em;
  font-size: 1.15em;
}

p {
  margin: 0.4em 0;
  text-indent: 2em;
}

p.no-indent {
  text-indent: 0;
}

img.cover {
  display: block;
  max-width: 100%;
  max-height: 100%;
  margin: 0 auto;
}
"""


@dataclass(frozen=True)
class TxtToEpubRule:
    level: int
    pattern: str
    use_full_line: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "pattern": self.pattern,
            "useFullLine": self.use_full_line,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "TxtToEpubRule":
        return cls(
            level=max(1, min(4, int(data.get("level", 1) or 1))),
            pattern=str(data.get("pattern", "")),
            use_full_line=bool(data.get("useFullLine") or data.get("use_full_line")),
        )


@dataclass(frozen=True)
class TxtToEpubTocEntry:
    id: str
    level: int
    title: str
    start_line: int
    end_line: int
    enabled: bool = True
    source_preview: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "enabled": self.enabled,
            "sourcePreview": self.source_preview,
            "confidence": self.confidence,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "TxtToEpubTocEntry":
        return cls(
            id=str(data.get("id", "")),
            level=max(1, min(4, int(data.get("level", 1) or 1))),
            title=str(data.get("title", "")),
            start_line=max(1, int(data.get("startLine", data.get("start_line", 1)) or 1)),
            end_line=max(1, int(data.get("endLine", data.get("end_line", 1)) or 1)),
            enabled=bool(data.get("enabled", True)),
            source_preview=str(data.get("sourcePreview", data.get("source_preview", ""))),
            confidence=_clamp_float(
                data.get("confidence"),
                default=1.0,
                low=0.0,
                high=1.0,
            ),
        )


def _clamp_float(value: object, *, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


@dataclass(frozen=True)
class TxtToEpubOptions:
    source_path: str
    output_dir: str = ""
    title: str = ""
    author: str = ""
    language: str = "zh"
    cover_path: str = ""
    style_id: str = "basic:classic"
    custom_css: str = ""
    overwrite: bool = False
    toc_entries: tuple[TxtToEpubTocEntry, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "output_dir": self.output_dir,
            "title": self.title,
            "author": self.author,
            "language": self.language,
            "cover_path": self.cover_path,
            "style_id": self.style_id,
            "custom_css": self.custom_css,
            "overwrite": self.overwrite,
            "toc_entries": [entry.to_dict() for entry in self.toc_entries],
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "TxtToEpubOptions":
        raw_entries = data.get("toc_entries", data.get("tocEntries", []))
        entries: list[TxtToEpubTocEntry] = []
        if isinstance(raw_entries, list):
            for raw in raw_entries:
                if isinstance(raw, Mapping):
                    entries.append(TxtToEpubTocEntry.from_mapping(raw))
        return cls(
            source_path=str(data.get("source_path", data.get("sourcePath", ""))),
            output_dir=str(data.get("output_dir", data.get("outputDir", ""))),
            title=str(data.get("title", "")),
            author=str(data.get("author", "")),
            language=str(data.get("language", "zh") or "zh"),
            cover_path=str(data.get("cover_path", data.get("coverPath", ""))),
            style_id=str(data.get("style_id", data.get("styleId", "basic:classic"))),
            custom_css=str(data.get("custom_css", data.get("customCss", ""))),
            overwrite=bool(data.get("overwrite", False)),
            toc_entries=tuple(entries),
        )


@dataclass(frozen=True)
class TxtToEpubAction:
    id: str
    source_path: str
    output_path: str
    options: TxtToEpubOptions
    selected: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "options": self.options.to_dict(),
            "selected": self.selected,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "TxtToEpubAction":
        options_raw = data.get("options", {})
        options = (
            TxtToEpubOptions.from_mapping(options_raw)
            if isinstance(options_raw, Mapping)
            else TxtToEpubOptions.from_mapping(data)
        )
        return cls(
            id=str(data.get("id", "")),
            source_path=str(data.get("source_path", data.get("sourcePath", options.source_path))),
            output_path=str(data.get("output_path", data.get("outputPath", ""))),
            options=options,
            selected=bool(data.get("selected", True)),
        )


@dataclass(frozen=True)
class TxtToEpubPlan:
    input_path: Path
    output_path: Path
    action: TxtToEpubAction

    def to_dict(self) -> dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "output_exists": self.output_path.exists(),
            "actions": [self.action.to_dict()],
            "totals": {"txt_files": 1},
        }


@dataclass(frozen=True)
class TxtToEpubResult:
    action_id: str
    source_path: str
    output_path: str
    status: str
    chapters_written: int = 0
    toc_entries: int = 0
    characters_written: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "status": self.status,
            "chapters_written": self.chapters_written,
            "toc_entries": self.toc_entries,
            "characters_written": self.characters_written,
            "error": self.error,
        }


_CJK_NUMBER = r"(?:[\d零〇一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟]+\s*)+"

NUMERIC_TITLE_RULES: tuple[TxtToEpubRule, ...] = (
    TxtToEpubRule(2, r"^\s*\d+(?:\.\d+)+(?:\s+(?P<title>.+?)|\s*)$"),
    TxtToEpubRule(1, r"^\s*\d+\s*[\.)、．]\s*(?P<title>.*?)\s*$"),
    TxtToEpubRule(2, r"^\s*(?P<title>\d{1,4})\s*$"),
)


PRESET_RULES: tuple[dict[str, object], ...] = (
    {
        "id": "markdown",
        "label": "Markdown 标题",
        "description": "#、##、###、#### 标题",
        "rules": (
            TxtToEpubRule(4, r"^\s*####\s*(?P<title>.+?)\s*$"),
            TxtToEpubRule(3, r"^\s*###\s*(?P<title>.+?)\s*$"),
            TxtToEpubRule(2, r"^\s*##\s*(?P<title>.+?)\s*$"),
            TxtToEpubRule(1, r"^\s*#\s*(?P<title>.+?)\s*$"),
        ),
    },
    {
        "id": "zh_novel",
        "label": "中文章节综合",
        "description": "网文、出版、番外/外传标题；兼容数字标题",
        "rules": (
            TxtToEpubRule(2, rf"^\s*(正文卷\s*)?第\s*{_CJK_NUMBER}\s*[章节回話话幕篇]\s*(?P<title>.*)$"),
            TxtToEpubRule(2, rf"^\s*(正文\s+)?第\s*{_CJK_NUMBER}\s*[章节回話话幕篇]\s*(?P<title>.*)$"),
            TxtToEpubRule(1, rf"^\s*(第\s*{_CJK_NUMBER}\s*[卷部篇册冊]|[卷部篇册冊]\s*{_CJK_NUMBER}|上卷|中卷|下卷|正文卷)\s*(?P<title>.*)$"),
            TxtToEpubRule(1, r"^\s*(序章|楔子|引子|序幕|尾声|后记|後記|前言|终章|終章|跋)\s*(?P<title>.*)$"),
            TxtToEpubRule(2, rf"^\s*(第\s*)?{_CJK_NUMBER}\s*[回节節幕]\s*(?P<title>.*)$"),
            TxtToEpubRule(2, rf"^\s*(番外|外传|外傳|特别篇|特別篇)\s*{_CJK_NUMBER}\s*(?P<title>.*)$"),
            TxtToEpubRule(1, r"^\s*(番外|外传|外傳|特别篇|特別篇|IF\s*线|if\s*线|if\s*線|IF\s*線)\s*(?P<title>.*)$"),
            *NUMERIC_TITLE_RULES,
        ),
    },
    {
        "id": "ko_novel",
        "label": "韩语小说",
        "description": "프롤로그、제1화、외전、에필로그；兼容数字标题",
        "rules": (
            TxtToEpubRule(1, r"^\s*(프롤로그|에필로그|서장|종장|후기|외전|번외|특별편)\s*(?P<title>.*)$"),
            TxtToEpubRule(1, r"^\s*\d+\s*(권|부|편)\s*(?P<title>.*)$"),
            TxtToEpubRule(1, r"^\s*제\s*\d+\s*(권|부|편)\s*(?P<title>.*)$"),
            TxtToEpubRule(2, r"^\s*제\s*\d+\s*(화|장|회)\s*(?P<title>.*)$"),
            TxtToEpubRule(2, r"^\s*\d+\s*(화|장|회)\s*(?P<title>.*)$"),
            *NUMERIC_TITLE_RULES,
        ),
    },
    {
        "id": "ja_novel",
        "label": "日文小说",
        "description": "プロローグ、第1話、外伝、エピローグ；兼容数字标题",
        "rules": (
            TxtToEpubRule(1, r"^\s*(プロローグ|エピローグ|序章|終章|前書き|まえがき|あとがき|外伝|番外|特別編|閑話|幕間)\s*(?P<title>.*)$"),
            TxtToEpubRule(1, rf"^\s*第\s*{_CJK_NUMBER}\s*(巻|卷|部|篇)\s*(?P<title>.*)$"),
            TxtToEpubRule(1, rf"^\s*{_CJK_NUMBER}\s*(巻|卷|部|篇)\s*(?P<title>.*)$"),
            TxtToEpubRule(2, rf"^\s*第\s*{_CJK_NUMBER}\s*(話|话|章|節|节|幕)\s*(?P<title>.*)$"),
            TxtToEpubRule(2, rf"^\s*{_CJK_NUMBER}\s*(話|话|章|節|节|幕)\s*(?P<title>.*)$"),
            *NUMERIC_TITLE_RULES,
        ),
    },
    {
        "id": "en_chapter",
        "label": "英文 Chapter",
        "description": "Chapter、Volume、Prologue、Epilogue；兼容数字标题",
        "rules": (
            TxtToEpubRule(1, r"^\s*(Prologue|Epilogue)\s*(?P<title>.*)$"),
            TxtToEpubRule(1, r"^\s*Volume\s+\d+\s*(?P<title>.*)$"),
            TxtToEpubRule(2, r"^\s*Chapter\s+\d+\s*(?P<title>.*)$"),
            *NUMERIC_TITLE_RULES,
        ),
    },
    {
        "id": "numeric",
        "label": "数字标题",
        "description": "1.、1.1、01、001",
        "rules": NUMERIC_TITLE_RULES,
    },
)
PRESET_ALIASES = {
    "zh_webnovel": "zh_novel",
    "zh_published": "zh_novel",
    "extra": "zh_novel",
}


def list_toc_presets() -> dict[str, object]:
    return {
        "presets": [
            {
                "id": str(preset["id"]),
                "label": str(preset["label"]),
                "description": str(preset["description"]),
                "rules": [rule.to_dict() for rule in _preset_rules(preset)],
            }
            for preset in PRESET_RULES
        ]
    }


def list_epub_styles() -> dict[str, object]:
    styles: list[dict[str, object]] = []
    for group in ("basic", "enhanced"):
        folder = _RESOURCE_ROOT / group
        if not folder.exists():
            continue
        for key in _VISIBLE_STYLE_KEYS[group]:
            path = folder / f"epub_style_{key}.css"
            if not path.exists():
                continue
            style_id = f"{group}:{_style_key(path)}"
            styles.append(
                {
                    "id": style_id,
                    "group": group,
                    "groupLabel": _STYLE_GROUPS[group],
                    "label": _style_label(path),
                    "description": _style_description(group),
                    "css": path.read_text(encoding="utf-8"),
                    "compatibility": "broad" if group == "basic" else "enhanced",
                }
            )
    return {"styles": styles, "template": _STYLE_TEMPLATE}


def scan_txt_toc(
    source_path: Path,
    *,
    preset_id: str = "markdown",
    custom_rules: Sequence[Mapping[str, object]] | None = None,
    advanced_pattern: str = "",
) -> dict[str, object]:
    source = source_path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError(f"TXT file does not exist: {source_path}")
    if source.suffix.lower() != _TXT_SUFFIX:
        raise ValueError(f"input file must be .txt: {source_path}")
    text = _read_text(source)
    lines = text.splitlines()
    rules = _resolve_rules(
        preset_id=preset_id,
        custom_rules=custom_rules or (),
        advanced_pattern=advanced_pattern,
    )
    compiled = _compile_rules(rules)
    entries: list[TxtToEpubTocEntry] = []
    for index, line in enumerate(lines, start=1):
        match = _match_line(line, compiled)
        if match is None:
            continue
        level, title = match
        clean_title = _clean_title(title or line)
        if not clean_title:
            clean_title = line.strip()
        entries.append(
            TxtToEpubTocEntry(
                id=f"toc-{len(entries):04d}",
                level=level,
                title=clean_title,
                start_line=index,
                end_line=index,
                enabled=True,
                source_preview=_line_preview(lines, index),
                confidence=_estimate_toc_confidence(
                    line=line,
                    title=clean_title,
                    level=level,
                ),
            )
        )
    return {
        "input_path": str(source),
        "title": source.stem,
        "line_count": len(lines),
        "character_count": len(text),
        "entries": [entry.to_dict() for entry in entries],
    }


def locate_txt_toc_entry(
    source_path: Path,
    *,
    query: str,
    level: int = 1,
    used_start_lines: Sequence[int] = (),
) -> dict[str, object]:
    source = source_path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError(f"TXT file does not exist: {source_path}")
    if source.suffix.lower() != _TXT_SUFFIX:
        raise ValueError(f"input file must be .txt: {source_path}")
    needle = _clean_title(query)
    if not needle:
        raise ValueError("TOC search text is required")
    lines = _read_text(source).splitlines()
    used = {line for line in used_start_lines if line >= 1}
    exact_matches: list[tuple[int, str]] = []
    contains_matches: list[tuple[int, str]] = []
    folded_needle = _fold_for_match(needle)
    for index, line in enumerate(lines, start=1):
        clean_line = _clean_title(line)
        if not clean_line:
            continue
        folded_line = _fold_for_match(clean_line)
        if folded_line == folded_needle:
            exact_matches.append((index, clean_line))
        elif folded_needle in folded_line:
            contains_matches.append((index, clean_line))
    for index, title in (*exact_matches, *contains_matches):
        if index not in used:
            return TxtToEpubTocEntry(
                id=f"toc-manual-{index}",
                level=max(1, min(4, int(level or 1))),
                title=title,
                start_line=index,
                end_line=index,
                enabled=True,
                source_preview=_line_preview(lines, index),
                confidence=0.9,
            ).to_dict()
    raise ValueError(f"cannot find TOC text in source TXT: {query}")


def build_txt_to_epub_plan(options: TxtToEpubOptions) -> TxtToEpubPlan:
    source = Path(options.source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError(f"TXT file does not exist: {options.source_path}")
    if source.suffix.lower() != _TXT_SUFFIX:
        raise ValueError(f"input file must be .txt: {options.source_path}")
    output_dir = (
        Path(options.output_dir).expanduser().resolve()
        if options.output_dir.strip()
        else source.parent
    )
    title = options.title.strip() or source.stem
    output_path = (output_dir / f"{_safe_filename(title)}{_EPUB_SUFFIX}").resolve()
    action = TxtToEpubAction(
        id="txt-epub-0000",
        source_path=str(source),
        output_path=str(output_path),
        options=options,
    )
    return TxtToEpubPlan(input_path=source, output_path=output_path, action=action)


def convert_txt_to_epub(action: TxtToEpubAction) -> TxtToEpubResult:
    source = Path(action.source_path).expanduser().resolve()
    output = Path(action.output_path).expanduser().resolve()
    try:
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"source TXT not found: {source}")
        if source.suffix.lower() != _TXT_SUFFIX:
            raise ValueError(f"source is not a TXT file: {source}")
        if output.exists() and not action.options.overwrite:
            raise FileExistsError(f"output EPUB already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        text = _read_text(source)
        css = _resolve_css(action.options.style_id, action.options.custom_css)
        _validate_css(css)
        chapters = _build_chapters(
            text,
            action.options.toc_entries,
            default_title=action.options.title.strip() or source.stem,
        )
        _write_epub(
            output,
            title=action.options.title.strip() or source.stem,
            author=action.options.author.strip(),
            language=action.options.language.strip() or "zh",
            css=css,
            chapters=chapters,
            cover_path=Path(action.options.cover_path).expanduser().resolve()
            if action.options.cover_path.strip()
            else None,
        )
        _validate_epub(output)
        return TxtToEpubResult(
            action_id=action.id,
            source_path=str(source),
            output_path=str(output),
            status="converted",
            chapters_written=len(chapters),
            toc_entries=sum(1 for chapter in chapters if chapter.enabled),
            characters_written=len(text),
        )
    except Exception as exc:  # noqa: BLE001
        return TxtToEpubResult(
            action_id=action.id,
            source_path=str(source),
            output_path=str(output),
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


def build_txt_to_epub_report(
    *,
    task_id: str,
    input_path: Path,
    generated_at: str,
    results: Sequence[TxtToEpubResult],
) -> dict[str, object]:
    converted = sum(1 for result in results if result.status == "converted")
    failed = sum(1 for result in results if result.status == "failed")
    return {
        "task_id": task_id,
        "generated_at": generated_at,
        "input_path": str(input_path),
        "totals": {
            "actions": len(results),
            "converted": converted,
            "failed": failed,
            "chapters_written": sum(result.chapters_written for result in results),
            "toc_entries": sum(result.toc_entries for result in results),
            "characters_written": sum(result.characters_written for result in results),
        },
        "results": [result.to_dict() for result in results],
    }


@dataclass(frozen=True)
class _Chapter:
    id: str
    title: str
    level: int
    body_lines: tuple[str, ...]
    enabled: bool
    show_heading: bool = True

    @property
    def filename(self) -> str:
        return f"chapter_{self.id}.xhtml"


@dataclass
class _NavNode:
    chapter: _Chapter
    children: list["_NavNode"]


def _resolve_rules(
    *,
    preset_id: str,
    custom_rules: Sequence[Mapping[str, object]],
    advanced_pattern: str,
) -> tuple[TxtToEpubRule, ...]:
    if advanced_pattern.strip():
        return (TxtToEpubRule(1, advanced_pattern.strip()),)
    rules = tuple(TxtToEpubRule.from_mapping(raw) for raw in custom_rules if raw)
    if rules:
        return rules
    resolved_preset_id = PRESET_ALIASES.get(preset_id, preset_id)
    for preset in PRESET_RULES:
        if preset["id"] == resolved_preset_id:
            return _preset_rules(preset)
    return _preset_rules(PRESET_RULES[0])


def _preset_rules(preset: Mapping[str, object]) -> tuple[TxtToEpubRule, ...]:
    use_full_line = preset["id"] != "markdown"
    return tuple(
        replace(rule, use_full_line=use_full_line)
        for rule in preset["rules"]  # type: ignore[index]
    )


def _compile_rules(
    rules: Sequence[TxtToEpubRule],
) -> tuple[tuple[int, re.Pattern[str], bool], ...]:
    compiled: list[tuple[int, re.Pattern[str], bool]] = []
    for rule in rules:
        if not rule.pattern.strip():
            continue
        compiled.append(
            (rule.level, re.compile(rule.pattern, re.IGNORECASE), rule.use_full_line)
        )
    if not compiled:
        raise ValueError("at least one chapter rule is required")
    return tuple(compiled)


def _match_line(
    line: str, compiled: Sequence[tuple[int, re.Pattern[str], bool]]
) -> tuple[int, str] | None:
    for level, pattern, use_full_line in compiled:
        match = pattern.match(line)
        if match is None:
            continue
        if use_full_line:
            return level, line.strip()
        title = ""
        if "title" in pattern.groupindex:
            title = match.group("title")
        elif match.groups():
            title = next((group for group in match.groups() if group), "")
        if not title:
            title = line.strip()
        return level, title
    return None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "cp949"):
        try:
            return raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _line_preview(lines: Sequence[str], line_number: int) -> str:
    start = max(1, line_number - 1)
    end = min(len(lines), line_number + 1)
    return "\n".join(lines[start - 1 : end]).strip()


def _clean_title(value: str) -> str:
    cleaned = value.strip(" \t　-—:：")
    return re.sub(r"\s+", " ", cleaned).strip()


def _estimate_toc_confidence(*, line: str, title: str, level: int) -> float:
    raw = line.strip()
    folded = _fold_for_match(raw)
    score = 0.86
    strong_heading = re.search(
        r"(?:^#+\s|第\s*[一二三四五六七八九十百千万〇零\d]+|章|卷|回|节|節|序章|楔子|尾声|尾聲|後記|后记|프롤로그|에필로그|외전|제\s*\d+|[0-9]+\s*(?:화|권|장)|chapter|volume|prologue|epilogue)",
        folded,
        re.IGNORECASE,
    )
    if strong_heading:
        score = 0.95
    elif re.fullmatch(r"\d+(?:\.\d+)*[.)、．]?", folded):
        score = 0.5
    elif re.match(r"^\d+(?:\.\d+)*[\s.)、．]+\S+", folded):
        score = 0.72
    if len(raw) > 80:
        score -= 0.25
    elif len(raw) > 48:
        score -= 0.12
    if level > 2:
        score -= 0.03 * (level - 2)
    if title and len(title) <= 3 and len(raw) > 24:
        score -= 0.1
    return round(max(0.2, min(1.0, score)), 2)


def _fold_for_match(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", normalized)
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    return safe or "output"


def _style_key(path: Path) -> str:
    name = path.stem
    for prefix in ("epub_style_",):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _style_label(path: Path) -> str:
    labels = {
        "classic": "经典",
        "warm": "暖色",
        "modern": "现代",
        "elegant": "雅致",
        "minimal": "极简",
        "grayscale": "灰阶",
        "monochrome": "黑白",
        "eyecare": "护眼",
        "contrast": "高对比",
        "soft": "柔和",
        "minimal_modern": "现代极简",
        "clean": "清爽",
        "geometric": "几何",
        "minimal_linear": "线性极简",
        "minimal_grid": "网格极简",
        "geometric_frame": "几何边框",
        "fantasy": "幻想",
        "line_hierarchy": "线性层级",
        "linear": "线性",
        "structured_minimal": "结构极简",
        "literary": "文学排版",
        "compact": "紧凑阅读",
        "spacious": "宽松阅读",
        "double_line": "双线标题",
        "sans_clean": "无衬线清爽",
        "framed": "框线章名",
        "sidebar": "侧栏强调",
        "structure_lines": "结构简约",
        "reader_modern": "阅读器现代",
        "soft_structure": "浅底结构",
    }
    key = _style_key(path)
    return labels.get(key, key.replace("_", " ").title())


def _style_description(group: str) -> str:
    if group == "basic":
        return "通用阅读器支持较好的基础排版。"
    return "包含更多装饰效果，部分阅读器可能忽略。"


def _resolve_css(style_id: str, custom_css: str) -> str:
    if style_id == "custom":
        return custom_css or _STYLE_TEMPLATE
    group, _, key = style_id.partition(":")
    if group not in _STYLE_GROUPS:
        group = "basic"
    if not key:
        key = "classic"
    path = _RESOURCE_ROOT / group / f"epub_style_{key}.css"
    if not path.exists():
        path = _RESOURCE_ROOT / "basic" / "epub_style_classic.css"
    return path.read_text(encoding="utf-8") if path.exists() else _STYLE_TEMPLATE


def _validate_css(css: str) -> None:
    lowered = css.lower()
    if "@import" in lowered:
        raise ValueError("custom CSS cannot use @import")
    if re.search(r"url\(\s*['\"]?\s*(https?:|file:|/)", lowered):
        raise ValueError("custom CSS cannot reference remote or absolute URLs")


def _build_chapters(
    text: str,
    toc_entries: Sequence[TxtToEpubTocEntry],
    *,
    default_title: str,
) -> tuple[_Chapter, ...]:
    lines = text.splitlines()
    entries = _validated_toc_entries(lines, toc_entries)
    chapters: list[_Chapter] = []
    if not entries:
        title, body_lines = _promote_first_text_line(tuple(lines), default_title)
        return (
            _Chapter(
                id="0001",
                title=title,
                level=1,
                body_lines=body_lines,
                enabled=True,
            ),
        )
    first = entries[0]
    prefix = tuple(lines[: max(0, first.start_line - 1)])
    if any(line.strip() for line in prefix):
        chapters.append(
            _Chapter(
                id=f"{len(chapters) + 1:04d}",
                title=_clean_title(default_title) or "前置内容",
                level=1,
                body_lines=prefix,
                enabled=False,
                show_heading=False,
            )
        )
    for index, entry in enumerate(entries):
        next_start = entries[index + 1].start_line if index + 1 < len(entries) else len(lines) + 1
        body_start = min(max(entry.start_line, 1), len(lines) + 1)
        body_end = min(max(next_start - 1, body_start), len(lines))
        # Exclude the heading line itself; the editable TOC title becomes the XHTML heading.
        body = tuple(lines[body_start:body_end])
        chapters.append(
            _Chapter(
                id=f"{len(chapters) + 1:04d}",
                title=entry.title.strip() or f"章节 {len(chapters) + 1}",
                level=entry.level,
                body_lines=body,
                enabled=entry.enabled,
            )
        )
    return tuple(chapters)


def _validated_toc_entries(
    lines: Sequence[str],
    toc_entries: Sequence[TxtToEpubTocEntry],
) -> tuple[TxtToEpubTocEntry, ...]:
    entries = tuple(entry for entry in toc_entries if entry.start_line >= 1)
    previous_line = 0
    for entry in entries:
        if entry.start_line > len(lines):
            raise ValueError(
                f"TOC line {entry.start_line} is outside source line count {len(lines)}"
            )
        if entry.start_line <= previous_line:
            raise ValueError("TOC entries must follow source line order")
        if not lines[entry.start_line - 1].strip():
            raise ValueError(f"TOC line {entry.start_line} is blank in source TXT")
        previous_line = entry.start_line
    return entries


def _promote_first_text_line(
    lines: tuple[str, ...],
    default_title: str,
) -> tuple[str, tuple[str, ...]]:
    for index, line in enumerate(lines):
        title = _clean_title(line)
        if title:
            return title, lines[:index] + lines[index + 1 :]
    return _clean_title(default_title), lines


def _write_epub(
    output: Path,
    *,
    title: str,
    author: str,
    language: str,
    css: str,
    chapters: Sequence[_Chapter],
    cover_path: Path | None,
) -> None:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cover_name = ""
    cover_media = ""
    cover_bytes: bytes | None = None
    if cover_path is not None:
        if not cover_path.exists() or not cover_path.is_file():
            raise FileNotFoundError(f"cover image not found: {cover_path}")
        cover_ext = cover_path.suffix.lower()
        if cover_ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("cover image must be jpg, jpeg, png, or webp")
        cover_name = f"cover{'.jpg' if cover_ext == '.jpeg' else cover_ext}"
        cover_media = mimetypes.guess_type(cover_name)[0] or "image/jpeg"
        cover_bytes = cover_path.read_bytes()

    with zipfile.ZipFile(output, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", _container_xml())
        zf.writestr("OEBPS/Styles/style.css", css)
        if cover_bytes is not None:
            zf.writestr(f"OEBPS/Images/{cover_name}", cover_bytes)
            zf.writestr("OEBPS/Text/cover.xhtml", _cover_xhtml(cover_name, title))
        for chapter in chapters:
            zf.writestr(f"OEBPS/Text/{chapter.filename}", _chapter_xhtml(chapter))
        zf.writestr(
            "OEBPS/nav.xhtml",
            _nav_xhtml(title=title, chapters=chapters),
        )
        zf.writestr(
            "OEBPS/toc.ncx",
            _toc_ncx(title=title, book_id=book_id, chapters=chapters),
        )
        zf.writestr(
            "OEBPS/content.opf",
            _opf(
                title=title,
                author=author,
                language=language,
                book_id=book_id,
                modified=now,
                chapters=chapters,
                cover_name=cover_name,
                cover_media=cover_media,
            ),
        )


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _cover_xhtml(cover_name: str, title: str) -> str:
    alt = html.escape(title, quote=True)
    src = html.escape(f"../Images/{cover_name}", quote=True)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh">
  <head>
    <title>{alt}</title>
    <link rel="stylesheet" type="text/css" href="../Styles/style.css"/>
  </head>
  <body>
    <section class="cover">
      <img class="cover" src="{src}" alt="{alt}"/>
    </section>
  </body>
</html>
"""


def _chapter_xhtml(chapter: _Chapter) -> str:
    title = html.escape(chapter.title, quote=False)
    heading = f"h{max(1, min(4, chapter.level))}"
    paragraphs = "\n".join(_paragraphs(chapter.body_lines))
    heading_markup = f"<{heading}>{title}</{heading}>" if chapter.show_heading else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh">
  <head>
    <title>{title}</title>
    <link rel="stylesheet" type="text/css" href="../Styles/style.css"/>
  </head>
  <body>
    <section class="chapter">
      {heading_markup}
      {paragraphs}
    </section>
  </body>
</html>
"""


def _paragraphs(lines: Sequence[str]) -> Iterable[str]:
    for line in lines:
        text = line.strip()
        if text:
            yield f"<p>{html.escape(text, quote=False)}</p>"


def _nav_xhtml(*, title: str, chapters: Sequence[_Chapter]) -> str:
    roots, _depth = _chapter_nav_tree(chapters)
    items = "\n".join(_render_nav_nodes(roots, indent=8))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">
  <head>
    <title>{html.escape(title, quote=False)}</title>
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>{html.escape(title, quote=False)}</h1>
      <ol>
{items}
      </ol>
    </nav>
  </body>
</html>
"""


def _toc_ncx(*, title: str, book_id: str, chapters: Sequence[_Chapter]) -> str:
    roots, depth = _chapter_nav_tree(chapters)
    play_order = [1]
    nav_points = list(_render_ncx_nodes(roots, play_order=play_order, indent=4))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{html.escape(book_id, quote=True)}"/>
    <meta name="dtb:depth" content="{depth}"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{html.escape(title, quote=False)}</text></docTitle>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>
"""


def _chapter_nav_tree(chapters: Sequence[_Chapter]) -> tuple[list[_NavNode], int]:
    roots: list[_NavNode] = []
    stack: list[tuple[int, _NavNode]] = []
    max_depth = 1
    for chapter in chapters:
        if not chapter.enabled:
            continue
        level = max(1, min(4, chapter.level))
        max_depth = max(max_depth, level)
        node = _NavNode(chapter=chapter, children=[])
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots, max_depth


def _render_nav_nodes(nodes: Sequence[_NavNode], *, indent: int) -> Iterable[str]:
    pad = " " * indent
    for node in nodes:
        chapter = node.chapter
        title = html.escape(chapter.title, quote=False)
        href = html.escape(f"Text/{chapter.filename}", quote=True)
        yield f'{pad}<li><a href="{href}">{title}</a>'
        if node.children:
            yield f"{pad}  <ol>"
            yield from _render_nav_nodes(node.children, indent=indent + 4)
            yield f"{pad}  </ol>"
        yield f"{pad}</li>"


def _render_ncx_nodes(
    nodes: Sequence[_NavNode], *, play_order: list[int], indent: int
) -> Iterable[str]:
    pad = " " * indent
    child_pad = " " * (indent + 2)
    for node in nodes:
        chapter = node.chapter
        current = play_order[0]
        play_order[0] += 1
        yield f'{pad}<navPoint id="navPoint-{current}" playOrder="{current}">'
        yield f"{child_pad}<navLabel><text>{html.escape(chapter.title, quote=False)}</text></navLabel>"
        yield f'{child_pad}<content src="Text/{html.escape(chapter.filename, quote=True)}"/>'
        if node.children:
            yield from _render_ncx_nodes(
                node.children, play_order=play_order, indent=indent + 2
            )
        yield f"{pad}</navPoint>"


def _opf(
    *,
    title: str,
    author: str,
    language: str,
    book_id: str,
    modified: str,
    chapters: Sequence[_Chapter],
    cover_name: str,
    cover_media: str,
) -> str:
    title_escaped = html.escape(title, quote=False)
    author_escaped = html.escape(author or "未知作者", quote=False)
    language_escaped = html.escape(language, quote=True)
    cover_meta = '<meta name="cover" content="cover-image"/>' if cover_name else ""
    cover_item = (
        f'<item id="cover-image" href="Images/{html.escape(cover_name, quote=True)}" media-type="{html.escape(cover_media, quote=True)}"/>'
        if cover_name
        else ""
    )
    cover_spine = '<itemref idref="cover"/>' if cover_name else ""
    cover_manifest = (
        '<item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>'
        if cover_name
        else ""
    )
    chapter_manifest = "\n    ".join(
        f'<item id="chap-{chapter.id}" href="Text/{html.escape(chapter.filename, quote=True)}" media-type="application/xhtml+xml"/>'
        for chapter in chapters
    )
    chapter_spine = "\n    ".join(
        f'<itemref idref="chap-{chapter.id}"/>' for chapter in chapters
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{html.escape(book_id, quote=False)}</dc:identifier>
    <dc:title>{title_escaped}</dc:title>
    <dc:creator>{author_escaped}</dc:creator>
    <dc:language>{language_escaped}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
    {cover_meta}
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="style" href="Styles/style.css" media-type="text/css"/>
    {cover_item}
    {cover_manifest}
    {chapter_manifest}
  </manifest>
  <spine toc="ncx">
    {cover_spine}
    {chapter_spine}
  </spine>
</package>
"""


def _validate_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        if not names or names[0] != "mimetype":
            raise ValueError("EPUB mimetype must be the first archive entry")
        required = {"META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml", "OEBPS/toc.ncx"}
        missing = sorted(required.difference(names))
        if missing:
            raise ValueError(f"generated EPUB is missing required entries: {missing}")
        opf = etree.fromstring(zf.read("OEBPS/content.opf"))
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        hrefs = [
            str(item.get("href"))
            for item in opf.xpath("//opf:manifest/opf:item", namespaces=ns)
            if item.get("href")
        ]
        for href in hrefs:
            full = f"OEBPS/{href}"
            if full not in names:
                raise ValueError(f"manifest item is missing from archive: {full}")


__all__ = [
    "TxtToEpubAction",
    "TxtToEpubOptions",
    "TxtToEpubRule",
    "build_txt_to_epub_plan",
    "build_txt_to_epub_report",
    "convert_txt_to_epub",
    "list_epub_styles",
    "list_toc_presets",
    "locate_txt_toc_entry",
    "scan_txt_toc",
]
