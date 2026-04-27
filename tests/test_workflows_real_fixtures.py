"""End-to-end tests against the real Korean novel fixtures in test/test-files/.

These confirm the full Translation and Glossary Extraction pipelines work on
the actual sample novels users will run the app against, not just on the
hand-crafted minimal fixtures used elsewhere in the suite. They use a fake
``ChatTransport`` so they're hermetic — no network, no real API keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

from transoria.domain import Language, TaskStatus
from transoria.formats.epub_parser import parse_epub_file
from transoria.formats.text import parse_txt_file
from transoria.llm import LlmClient, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import TaskCache
from transoria.workflows.glossary import (
    GLOSSARY_FILENAME_JSON,
    GLOSSARY_FILENAME_REFERENCES,
    GLOSSARY_FILENAME_XLSX,
    GlossaryConfig,
    GlossaryOrchestrator,
)
from transoria.workflows.translation import (
    Glossary,
    TranslationConfig,
    TranslationOrchestrator,
)


REAL_FIXTURE_DIR = Path("test/test-files")
REAL_EPUB = REAL_FIXTURE_DIR / "[몽년] 스노우 화이트 1권 @공이.epub"
REAL_TXT = REAL_FIXTURE_DIR / "블랙 앤 그레이(BLACK ＆ GREY) 1권.txt"
TRANSLATION_MARKER = "翻译:"


pytestmark = pytest.mark.skipif(
    not REAL_FIXTURE_DIR.is_dir(), reason="real fixtures not present"
)


@dataclass
class EchoTranslateTransport:
    """Marks every JSONL line with TRANSLATION_MARKER, returning the same indices."""

    requests: int = field(default=0, init=False)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests += 1
        user_message = payload["messages"][-1]["content"]
        translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
        lines: list[str] = []
        for line in translate_section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = json.loads(stripped)
            for key, value in parsed.items():
                lines.append(
                    json.dumps({key: f"{TRANSLATION_MARKER}{value}"}, ensure_ascii=False)
                )
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        return TransportResult(200, body)


@dataclass
class GlossaryEmittingTransport:
    """Returns a fixed roster of glossary candidates per chunk.

    Real chunks of a Korean novel produce dozens of unique candidates in
    practice; for the test we just need the pipeline to run end-to-end and
    emit the three artifacts. The candidates are crafted so frequency
    counting in the real text yields non-zero matches.
    """

    candidates: tuple[tuple[str, str, str], ...] = (
        ("스노우", "雪", "Title Term"),
        ("화이트", "怀特", "Title Term"),
        ("공이", "孔二", "Author"),
    )
    requests: int = field(default=0, init=False)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests += 1
        lines = [
            json.dumps({"src": src, "dst": dst, "type": info}, ensure_ascii=False)
            for src, dst, info in self.candidates
        ]
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 17},
        }
        return TransportResult(200, body)


def _model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="model-x",
        api_keys=("key",),
        thinking_level=ThinkingLevel.OFF,
        concurrency_limit=4,
        rpm_limit=0,
    )


_TIMES = iter(range(600))


def _frozen_clock() -> str:
    return f"2026-04-27T00:01:{next(_TIMES, 599) % 60:02d}+00:00"


def _binary_assets(epub_path: Path) -> dict[str, str]:
    """Map of zip member → sha1 digest for non-XML binary entries."""

    digests: dict[str, str] = {}
    with zipfile.ZipFile(epub_path) as archive:
        for info in archive.infolist():
            lower = info.filename.lower()
            if (
                lower.endswith(".xhtml")
                or lower.endswith(".html")
                or lower.endswith(".htm")
                or lower.endswith(".opf")
                or lower.endswith(".ncx")
                or lower.endswith(".xml")
                or info.is_dir()
            ):
                continue
            digests[info.filename] = hashlib.sha1(
                archive.read(info.filename)
            ).hexdigest()
    return digests


def _build_translation_config(input_dir: Path, output_dir: Path) -> TranslationConfig:
    return TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=_model(),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        chunk_size=16,
        context_line_count=2,
    )


def _new_translation_orchestrator(
    transport: EchoTranslateTransport, cache_root: Path
) -> TranslationOrchestrator:
    counter = iter(range(2000))

    return TranslationOrchestrator(
        cache=TaskCache(root=cache_root),
        client=LlmClient(transport=transport),
        clock=_frozen_clock,
        id_factory=lambda: f"task-{next(counter):04d}",
    )


def _new_glossary_orchestrator(
    transport: GlossaryEmittingTransport, cache_root: Path
) -> GlossaryOrchestrator:
    counter = iter(range(2000))

    return GlossaryOrchestrator(
        cache=TaskCache(root=cache_root),
        client=LlmClient(transport=transport),
        clock=_frozen_clock,
        id_factory=lambda: f"task-{next(counter):04d}",
    )


# ---------------------------------------------------------------------------
# Real-fixture Translation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_EPUB.exists(), reason="real EPUB fixture missing")
def test_translation_pipeline_round_trips_real_epub(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    sample_path = input_dir / REAL_EPUB.name
    sample_path.write_bytes(REAL_EPUB.read_bytes())

    binary_digests_before = _binary_assets(sample_path)

    transport = EchoTranslateTransport()
    orchestrator = _new_translation_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(_build_translation_config(input_dir, output_dir))
    )

    assert result.final_status is TaskStatus.COMPLETED
    epub_outputs = [path for path in result.translated_outputs if path.suffix == ".epub"]
    assert len(epub_outputs) == 1
    out_path = epub_outputs[0]
    assert out_path.name.endswith("-zh.epub")

    # Every body segment carries the translation marker.
    out_doc = parse_epub_file(out_path)
    body_texts = [seg.text for seg in out_doc.segments if seg.kind.value == "body"]
    assert body_texts
    assert all(TRANSLATION_MARKER in text for text in body_texts), (
        "some body segments were not translated"
    )

    # Source EPUB structure is preserved: same package layout and spine count.
    src_doc = parse_epub_file(sample_path)
    assert out_doc.package.opf_path == src_doc.package.opf_path
    assert out_doc.package.spine_paths == src_doc.package.spine_paths
    assert out_doc.package.ncx_path == src_doc.package.ncx_path

    # Untouched binary assets are byte-identical between source and output.
    binary_digests_after = _binary_assets(out_path)
    assert binary_digests_after == binary_digests_before


@pytest.mark.skipif(not REAL_TXT.exists(), reason="real TXT fixture missing")
def test_translation_pipeline_translates_real_txt(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    sample_path = input_dir / REAL_TXT.name
    sample_path.write_bytes(REAL_TXT.read_bytes())

    source_doc = parse_txt_file(sample_path)
    expected_translatable = sum(
        1 for segment in source_doc.segments if segment.text.strip()
    )

    transport = EchoTranslateTransport()
    orchestrator = _new_translation_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(_build_translation_config(input_dir, output_dir))
    )

    assert result.final_status is TaskStatus.COMPLETED
    txt_outputs = [path for path in result.translated_outputs if path.suffix == ".txt"]
    assert len(txt_outputs) == 1
    out_path = txt_outputs[0]
    assert out_path.name.endswith("-zh.txt")

    out_text = out_path.read_text(encoding="utf-8")
    # Every non-empty source line should have been translated → marker count
    # must equal the number of translatable lines.
    assert out_text.count(TRANSLATION_MARKER) == expected_translatable
    assert result.statistics.completed_segments == expected_translatable


# ---------------------------------------------------------------------------
# Real-fixture Glossary Extraction
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_EPUB.exists(), reason="real EPUB fixture missing")
def test_glossary_pipeline_emits_three_artifacts_for_real_epub(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    sample_path = input_dir / REAL_EPUB.name
    sample_path.write_bytes(REAL_EPUB.read_bytes())

    transport = GlossaryEmittingTransport()
    orchestrator = _new_glossary_orchestrator(transport, tmp_path / "cache")

    result = asyncio.run(
        orchestrator.run(
            GlossaryConfig(
                input_dir=input_dir,
                output_dir=output_dir,
                source_language=Language.KOREAN,
                target_language=Language.CHINESE_SIMPLIFIED,
                model=_model(),
                prompt_preset=default_preset(PromptKind.GLOSSARY),
                chunk_char_limit=4000,
                min_frequency=1,
            )
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert len(result.glossary_outputs_per_file) == 1
    xlsx_path, json_path, references_path = result.glossary_outputs_per_file[0]
    expected_basename = sample_path.stem
    assert xlsx_path.name == f"{expected_basename}{GLOSSARY_FILENAME_XLSX}"
    assert json_path.name == f"{expected_basename}{GLOSSARY_FILENAME_JSON}"
    assert references_path.name == f"{expected_basename}{GLOSSARY_FILENAME_REFERENCES}"
    for path in (xlsx_path, json_path, references_path):
        assert path.exists() and path.stat().st_size > 0

    # The transport seeds three candidates; at least one must survive
    # frequency filtering against the real source text.
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload, "glossary JSON should contain at least one entry"
    references_text = references_path.read_text(encoding="utf-8")
    assert "原文:" in references_text
    assert "出现次数:" in references_text
