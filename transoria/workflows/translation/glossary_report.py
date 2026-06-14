"""Report glossary term application after translation completes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from transoria.domain import SubtaskStatus
from transoria.runtime.subtask import Subtask
from transoria.workflows.translation.rules import Glossary, GlossaryEntry


GLOSSARY_REPORT_FILENAME_JSON = "translation-glossary-report.json"
GLOSSARY_REPORT_FILENAME_MD = "translation-glossary-report.md"


@dataclass(frozen=True)
class GlossaryApplicationRecord:
    segment_id: str
    src: str
    dst: str
    info: str
    applied: bool
    source_text: str
    translated_text: str


@dataclass(frozen=True)
class GlossaryApplicationReport:
    total_matches: int
    applied_matches: int
    missing_matches: int
    segments_with_matches: int
    segments_with_missing_terms: int
    records: tuple[GlossaryApplicationRecord, ...]


@dataclass(frozen=True)
class GlossaryApplicationReportPaths:
    json_path: Path
    markdown_path: Path


def build_glossary_application_report(
    subtasks: Sequence[Subtask],
    translations_by_segment: Mapping[str, str],
) -> GlossaryApplicationReport:
    """Build a report from cached subtask payloads and final translations.

    Chunk payloads contain the terms offered to the model for that chunk.
    This report narrows them back down per segment by re-running local
    glossary matching against each segment's prompt/original text, then checks
    whether the configured target term appears in the final translated text.
    """

    records_by_key: dict[
        tuple[str, str, str, str], GlossaryApplicationRecord
    ] = {}
    for subtask in subtasks:
        if subtask.status is not SubtaskStatus.COMPLETED:
            continue
        payload = subtask.request_payload
        if not isinstance(payload, Mapping):
            continue
        entries = _payload_glossary_entries(payload.get("glossary_entries"))
        if not entries:
            continue
        glossary = Glossary(entries=tuple(entries))
        raw_segments = payload.get("segments")
        if not isinstance(raw_segments, Sequence) or isinstance(
            raw_segments, (str, bytes)
        ):
            continue
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, Mapping):
                continue
            segment_id = str(raw_segment.get("segment_id", ""))
            if not segment_id or segment_id not in translations_by_segment:
                continue
            prompt_text = str(raw_segment.get("prompt_text", ""))
            original_text = str(raw_segment.get("original_text", ""))
            source_text = original_text or prompt_text
            matched = tuple(glossary.match(prompt_text))
            if not matched and original_text and original_text != prompt_text:
                matched = tuple(glossary.match(original_text))
            if not matched:
                continue
            translated_text = translations_by_segment[segment_id]
            for entry in matched:
                key = (segment_id, entry.src, entry.dst, entry.info)
                records_by_key[key] = GlossaryApplicationRecord(
                    segment_id=segment_id,
                    src=entry.src,
                    dst=entry.dst,
                    info=entry.info,
                    applied=_target_term_present(entry, translated_text),
                    source_text=source_text,
                    translated_text=translated_text,
                )

    records = tuple(
        records_by_key[key] for key in sorted(records_by_key, key=lambda item: item[0])
    )
    applied = sum(1 for record in records if record.applied)
    missing = len(records) - applied
    return GlossaryApplicationReport(
        total_matches=len(records),
        applied_matches=applied,
        missing_matches=missing,
        segments_with_matches=len({record.segment_id for record in records}),
        segments_with_missing_terms=len(
            {record.segment_id for record in records if not record.applied}
        ),
        records=records,
    )


def write_glossary_application_report(
    report: GlossaryApplicationReport, output_dir: Path
) -> GlossaryApplicationReportPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / GLOSSARY_REPORT_FILENAME_JSON
    markdown_path = output_dir / GLOSSARY_REPORT_FILENAME_MD
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return GlossaryApplicationReportPaths(
        json_path=json_path,
        markdown_path=markdown_path,
    )


def _payload_glossary_entries(raw: object) -> tuple[GlossaryEntry, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    entries: list[GlossaryEntry] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        src = str(item.get("src", "")).strip()
        dst = str(item.get("dst", "")).strip()
        if not src or not dst:
            continue
        entries.append(
            GlossaryEntry(
                src=src,
                dst=dst,
                info=str(item.get("info", "")),
                regex=bool(item.get("regex", False)),
                case_sensitive=bool(item.get("case_sensitive", False)),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return tuple(entries)


def _target_term_present(entry: GlossaryEntry, translated_text: str) -> bool:
    expected = entry.dst.strip()
    if not expected:
        return False
    if entry.case_sensitive:
        return expected in translated_text
    return expected.casefold() in translated_text.casefold()


def _render_markdown(report: GlossaryApplicationReport) -> str:
    lines = [
        "# 术语应用报告",
        "",
        "本报告只基于已完成任务缓存、本地术语匹配和最终译文生成；不会额外调用模型，也不会修改译文。",
        "",
        f"- 术语匹配总数：{report.total_matches}",
        f"- 译文包含目标译名：{report.applied_matches}",
        f"- 疑似未应用：{report.missing_matches}",
        f"- 涉及段落：{report.segments_with_matches}",
        f"- 含疑似未应用术语的段落：{report.segments_with_missing_terms}",
        "",
    ]
    missing = [record for record in report.records if not record.applied]
    if missing:
        lines.extend(["## 疑似未应用", ""])
        for record in missing:
            lines.extend(_record_lines(record))
    else:
        lines.extend(["## 疑似未应用", "", "无。", ""])
    applied = [record for record in report.records if record.applied]
    if applied:
        lines.extend(["## 已应用", ""])
        for record in applied:
            lines.extend(_record_lines(record))
    return "\n".join(lines).rstrip() + "\n"


def _record_lines(record: GlossaryApplicationRecord) -> list[str]:
    info = f"（{record.info}）" if record.info else ""
    return [
        f"- `{record.segment_id}` `{record.src}` -> `{record.dst}`{info}",
        f"  - 原文：{_preview(record.source_text)}",
        f"  - 译文：{_preview(record.translated_text)}",
        "",
    ]


def _preview(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


__all__ = [
    "GLOSSARY_REPORT_FILENAME_JSON",
    "GLOSSARY_REPORT_FILENAME_MD",
    "GlossaryApplicationRecord",
    "GlossaryApplicationReport",
    "GlossaryApplicationReportPaths",
    "build_glossary_application_report",
    "write_glossary_application_report",
]
