"""Tests for the Phase 1.8 recursive-review improvements."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

from transoria.domain import Language, TaskStatus
from transoria.formats.text import TXT_ENCODING_CANDIDATES, parse_txt_file
from transoria.llm import LlmClient, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.client import TransportResult
from transoria.prompts import (
    PromptKind,
    PromptPreset,
    PromptPresetStore,
    default_preset,
)
from transoria.runtime import TaskCache
from transoria.workflows.fake_name import FakeNameRoster
from transoria.workflows.glossary import (
    Candidate,
    GlossaryConfig,
    GlossaryOrchestrator,
    GlossaryRecord,
)
from transoria.workflows.glossary.combine import combine_glossary_records
from transoria.workflows.glossary.statistics import (
    GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS,
)
from transoria.workflows.translation import (
    Glossary,
    TranslationConfig,
    TranslationOrchestrator,
    evaluate_segment_confidence,
)
# #1: buffer_epub_archives default OFF (memory-conscious)


def test_translation_config_default_does_not_buffer_epub_archives() -> None:
    config = TranslationConfig(
        input_dir=Path("/in"),
        output_dir=Path("/out"),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://x/",
            model_id="m",
            api_keys=("k",),
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
    )

    assert config.buffer_epub_archives is False
# #3: TXT encoding expansion


@pytest.mark.parametrize(
    "encoding, paragraph",
    [
        # Each paragraph is long enough for chardet to detect with
        # confidence; mirrors a realistic novel-line workload rather than a
        # 2-character probe.
        ("gbk", "他在房间里走来走去，思考着今天发生的所有事情。" * 10),
        ("gb18030", "她坐在窗边看着外面的雪花飘落，心情格外平静。" * 10),
        ("big5", "他在房間裡走來走去，思考著今天發生的所有事情。" * 10),
        ("shift_jis", "彼は部屋の中を歩き回り、今日起こったすべてのことについて考えていた。" * 10),
    ],
)
def test_parse_txt_file_decodes_chinese_and_japanese_encodings(
    tmp_path: Path, encoding: str, paragraph: str
) -> None:
    assert encoding in TXT_ENCODING_CANDIDATES
    path = tmp_path / f"sample-{encoding}.txt"
    path.write_bytes(paragraph.encode(encoding))

    document = parse_txt_file(path)

    full_text = "".join(segment.text for segment in document.segments)
    # We can't insist on byte-for-byte equality (chardet may pick a
    # superset like GB18030 for GBK input), but the unique characters
    # in the source should round-trip into the decoded text.
    sample_chars = set(paragraph)
    decoded_chars = set(full_text)
    overlap = sample_chars & decoded_chars
    assert len(overlap) >= len(sample_chars) // 2, (
        f"chardet picked the wrong encoding for {encoding}: "
        f"only {len(overlap)} / {len(sample_chars)} chars survived"
    )
# #5: PromptPresetStore atomic write


def test_prompt_preset_store_atomic_write_does_not_leave_tmp(tmp_path: Path) -> None:
    store = PromptPresetStore(
        path=tmp_path / "prompts.translation.json", kind=PromptKind.TRANSLATION
    )
    custom = PromptPreset(
        id="cust",
        name="Custom",
        kind=PromptKind.TRANSLATION,
        system_prompt="hi {target_language}",
    )

    store.save([default_preset(PromptKind.TRANSLATION), custom])

    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []
# #7: combined-glossary references round-robin


def test_combine_round_robin_distributes_references_across_sources() -> None:
    file_a = (
        GlossaryRecord(
            src="신해범",
            dst="申海范",
            info="",
            frequency=10,
            references=("a-line-1", "a-line-2", "a-line-3"),
        ),
    )
    file_b = (
        GlossaryRecord(
            src="신해범",
            dst="申海范",
            info="",
            frequency=10,
            references=("b-line-1", "b-line-2", "b-line-3"),
        ),
    )
    file_c = (
        GlossaryRecord(
            src="신해범",
            dst="申海范",
            info="",
            frequency=10,
            references=("c-line-1", "c-line-2"),
        ),
    )

    combined = combine_glossary_records(
        [file_a, file_b, file_c], reference_example_limit=4
    )

    refs = combined[0].references
    # Round-robin selects one per source per round; with 4 slots and 3
    # sources, we get a-1, b-1, c-1, then a-2.
    assert refs == ("a-line-1", "b-line-1", "c-line-1", "a-line-2")
# #8: confidence CJK quotes


def test_confidence_flags_dropped_cjk_quotes() -> None:
    source = "그가 「잘 가」 라고 말했다."  # Korean dialogue with CJK quotes
    translation = "他说再见。"  # quotes dropped, no fullwidth dialogue marks

    verdict = evaluate_segment_confidence(
        source,
        translation,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=1,
    )

    assert verdict.is_low_confidence
    assert any("punctuation" in reason for reason in verdict.reasons)
# #4: glossary failed-subtasks file


@dataclass
class FailingTransport:
    fail_call_indices: tuple[int, ...] = ()
    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        index = len(self.requests)
        self.requests.append(dict(payload))
        if index in self.fail_call_indices:
            return TransportResult(500, {"error": "boom"})
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"src":"신해범","dst":"申海范","type":"Male"}',
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return TransportResult(200, body)


def test_glossary_orchestrator_writes_failed_subtasks_file_when_chunks_fail(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Sample.txt").write_text("신해범\n", encoding="utf-8")

    transport = FailingTransport(fail_call_indices=(0,))
    orchestrator = GlossaryOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-failing",
    )

    result = asyncio.run(
        orchestrator.run(
            GlossaryConfig(
                input_dir=input_dir,
                output_dir=output_dir,
                source_language=Language.KOREAN,
                target_language=Language.CHINESE_SIMPLIFIED,
                model=ModelConfig(
                    id="m",
                    display_name="m",
                    provider_format=ProviderFormat.OPENAI,
                    base_url="https://x/",
                    model_id="m",
                    api_keys=("k",),
                ),
                prompt_preset=default_preset(PromptKind.GLOSSARY),
                chunk_char_limit=200,
                request_retry_attempts=0,
            )
        )
    )

    failed_path = result.statistics_path.parent / GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS
    assert failed_path.exists()
    assert not (output_dir / GLOSSARY_STATISTICS_FILENAME_FAILED_SUBTASKS).exists()
    body = failed_path.read_text(encoding="utf-8")
    assert "subtask:" in body
    assert "error:" in body
# #10: "Everything on" integration test


@dataclass
class EverythingTransport:
    """Acts as an OpenAI-format streaming-aware transport for translation chunks."""

    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests.append(dict(payload))
        user_message = payload["messages"][-1]["content"]
        translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
        lines: list[str] = []
        for line in translate_section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("{"):
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            for key, value in parsed.items():
                lines.append(json.dumps({key: f"翻译:{value}"}, ensure_ascii=False))
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        }
        return TransportResult(200, body)


def test_translation_pipeline_with_every_feature_simultaneously(tmp_path: Path) -> None:
    """Retry + streaming + thinking + confidence + fake-name + TPM all on,
    against a small synthetic source. Catches emergent bugs that per-feature
    tests can't see."""

    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Mixed.txt").write_text(
        "신해범 walks into the room.\nA short line.\n寒冷 winter day.\n",
        encoding="utf-8",
    )

    transport = EverythingTransport()
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-everything",
    )
    config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://x/",
            model_id="m",
            api_keys=("k",),
            thinking_level=ThinkingLevel.MEDIUM,
            tpm_limit=10_000,
            rpm_limit=0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.from_records(
            [{"src": "신해범", "dst": "申海范", "info": "Male Name"}]
        ),
        chunk_size=2,
        context_line_count=1,
        stream=True,
        enable_confidence_check=True,
        min_length_ratio=0.1,
        max_length_ratio=10.0,
        fake_name_roster=FakeNameRoster(mapping={"寒": "ZCHN"}),
        bilingual_enabled=True,
        buffer_epub_archives=False,
    )

    result = asyncio.run(orchestrator.run(config))

    assert result.final_status is TaskStatus.COMPLETED
    assert len(result.translated_outputs) == 1
    body = result.translated_outputs[0].read_text(encoding="utf-8")
    # Korean lines are sent to the model; non-Korean lines are preserved by
    # source-language filtering instead of spending a request on them.
    assert body.count("翻译:") == 2
    assert "신해범 walks into the room." in body
    # The fake-name placeholder is restored to the original character.
    assert "寒" in body
    # Glossary entry was injected into at least one user prompt.
    glossary_seen = any(
        "신해범 -> 申海范" in str(req["messages"][-1]["content"])
        for req in transport.requests
    )
    assert glossary_seen
    # Streaming flag was set on every chat call.
    assert all(req.get("stream") is True for req in transport.requests)
