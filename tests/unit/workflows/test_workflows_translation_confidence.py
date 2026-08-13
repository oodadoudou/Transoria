from __future__ import annotations

import asyncio
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

from transoria.domain import Language, SubtaskStatus, TaskStatus
from transoria.llm import LlmClient, ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import TaskCache
from transoria.runtime.subtask import Subtask
from transoria.workflows.translation import (
    Glossary,
    TranslationConfig,
    TranslationOrchestrator,
    evaluate_segment_confidence,
)
from transoria.workflows.translation.confidence import (
    TAG_FUNCTION_WORD_RESIDUE,
    TAG_MODEL_CHATTER,
    TAG_PUNCTUATION_ANOMALY,
    TAG_SOURCE_RESIDUE,
    TAG_TARGET_LANGUAGE_WEAK,
    TAG_TRUNCATED,
    TAG_VERBATIM_ECHO,
)
from transoria.workflows.translation.orchestrator import _collect_translations
from transoria.workflows.translation.segment_state import (
    PRESERVED_CANDIDATE_SEGMENTS_KEY,
    collect_segment_state_from_authoritative_subtasks,
    mark_accepted_override,
)


CONFIDENCE_FIXTURE_DIR = Path("tests/fixtures/public/translation_confidence")


def _completed_translation_subtask(
    subtask_id: str, payload: Mapping[str, object]
) -> Subtask:
    return Subtask(
        id=subtask_id,
        task_id="task-confidence",
        status=SubtaskStatus.COMPLETED,
        response_content=json.dumps(payload, ensure_ascii=False),
    )


def _translation_subtask(
    subtask_id: str, status: SubtaskStatus, payload: Mapping[str, object]
) -> Subtask:
    return Subtask(
        id=subtask_id,
        task_id="task-confidence",
        status=status,
        response_content=json.dumps(payload, ensure_ascii=False),
    )


def test_collect_translations_clears_stale_low_confidence_after_clean_retry() -> None:
    first = _completed_translation_subtask(
        "chunk-00000",
        {
            "version": 2,
            "translations": {"0:0": "안녕"},
            "low_confidence": [
                {
                    "segment_id": "0:0",
                    "reasons": ["source residue remains"],
                    "tags": ["source_residue"],
                }
            ],
        },
    )
    retry = _completed_translation_subtask(
        "chunk-00000.s1",
        {"version": 2, "translations": {"0:0": "你好"}, "low_confidence": []},
    )

    translations, low_confidence = _collect_translations((first, retry))

    assert translations == {"0:0": "你好"}
    assert low_confidence == []


def test_collect_translations_keeps_latest_low_confidence_for_updated_segment() -> None:
    first = _completed_translation_subtask(
        "chunk-00000",
        {"version": 2, "translations": {"0:0": "你好"}, "low_confidence": []},
    )
    retry = _completed_translation_subtask(
        "chunk-00000.s1",
        {
            "version": 2,
            "translations": {"0:0": "안녕"},
            "low_confidence": [
                {
                    "segment_id": "0:0",
                    "reasons": ["source residue remains"],
                    "tags": ["source_residue"],
                }
            ],
        },
    )

    translations, low_confidence = _collect_translations((first, retry))

    assert translations == {"0:0": "안녕"}
    assert low_confidence == [
        {"segment_id": "0:0", "reasons": ["source residue remains"]}
    ]


def test_collect_translations_legacy_flat_response_clears_stale_low_confidence() -> None:
    first = _completed_translation_subtask(
        "chunk-00000",
        {
            "version": 2,
            "translations": {"0:0": "안녕"},
            "low_confidence": [
                {
                    "segment_id": "0:0",
                    "reasons": ["source residue remains"],
                    "tags": ["source_residue"],
                }
            ],
        },
    )
    legacy_retry = _completed_translation_subtask("chunk-00000.s1", {"0:0": "你好"})

    translations, low_confidence = _collect_translations((first, legacy_retry))

    assert translations == {"0:0": "你好"}
    assert low_confidence == []


def test_authoritative_state_ignores_unaccepted_failed_payload() -> None:
    failed = _translation_subtask(
        "chunk-00000",
        SubtaskStatus.FAILED,
        {
            "version": 2,
            "translations": {"0:0": "안녕"},
            "low_confidence": [
                {
                    "segment_id": "0:0",
                    "reasons": ["source residue remains"],
                    "tags": ["source_residue"],
                }
            ],
        },
    )

    translations, low_confidence = collect_segment_state_from_authoritative_subtasks(
        (failed,)
    )

    assert translations == {}
    assert low_confidence == {}


def test_authoritative_state_keeps_accepted_failed_override() -> None:
    payload: dict[str, object] = {
        "version": 2,
        "translations": {"0:0": "你好", "0:1": "산"},
        "low_confidence": [
            {
                "segment_id": "0:0",
                "reasons": ["manual review accepted"],
                "tags": ["manual_review"],
            },
            {
                "segment_id": "0:1",
                "reasons": ["source residue remains"],
                "tags": ["source_residue"],
            },
        ],
    }
    mark_accepted_override(payload, "0:0")
    failed = _translation_subtask("chunk-00000", SubtaskStatus.FAILED, payload)

    translations, low_confidence = collect_segment_state_from_authoritative_subtasks(
        (failed,)
    )

    assert translations == {"0:0": "你好"}
    assert low_confidence == {
        "0:0": {"reasons": ["manual review accepted"], "tags": ["manual_review"]}
    }


def test_authoritative_state_keeps_preserved_failed_candidate_for_proofreading() -> None:
    failed = _translation_subtask(
        "chunk-00000",
        SubtaskStatus.FAILED,
        {
            "version": 2,
            "translations": {
                "0:0": "[全体]닉네임：已经翻译的正文",
                "0:1": "아직 원문",
            },
            PRESERVED_CANDIDATE_SEGMENTS_KEY: ["0:0"],
            "low_confidence": [
                {
                    "segment_id": "0:0",
                    "reasons": ["force_accepted_after_max_retries"],
                    "tags": ["source_residue"],
                }
            ],
        },
    )

    translations, low_confidence = collect_segment_state_from_authoritative_subtasks(
        (failed,)
    )

    assert translations == {"0:0": "[全体]닉네임：已经翻译的正文"}
    assert low_confidence == {
        "0:0": {
            "reasons": ["force_accepted_after_max_retries"],
            "tags": ["source_residue"],
        }
    }


def test_evaluate_flags_excessive_length_inflation() -> None:
    verdict = evaluate_segment_confidence(
        "short",
        "x" * 100,
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert verdict.is_low_confidence
    assert any("length ratio" in reason for reason in verdict.reasons)


def test_evaluate_flags_excessive_truncation() -> None:
    verdict = evaluate_segment_confidence(
        "this is a substantial source line",
        "ok",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert verdict.is_low_confidence


def test_evaluate_flags_punctuation_delta() -> None:
    verdict = evaluate_segment_confidence(
        "first sentence. second one. third! and fourth?",
        "single output sentence with no terminators",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=2,
    )

    assert verdict.is_low_confidence
    assert any("punctuation" in reason for reason in verdict.reasons)


def test_evaluate_passes_normal_translation() -> None:
    verdict = evaluate_segment_confidence(
        "신해범 walked into the room.",
        "申海范走进了房间。",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert not verdict.is_low_confidence


def test_evaluate_skips_when_either_side_blank() -> None:
    assert not evaluate_segment_confidence(
        "",
        "translated",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    ).is_low_confidence


def test_evaluate_flags_empty_translation_for_nonempty_source() -> None:
    verdict = evaluate_segment_confidence(
        "This line needs translation.",
        "",
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    assert verdict.is_low_confidence
    assert any("empty translation" in reason for reason in verdict.reasons)


def test_evaluate_flags_truncated_translation_when_source_ends_sentence() -> None:
    verdict = evaluate_segment_confidence(
        "다급해진 진우가 입을 막고 있던 손을 떼어냈다.",
        "听到这句话，镇宇慌忙摇头，但对方根本充耳不闻，反而更快地将阴茎往里面猛插。情急之下，镇宇掰",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert any("truncated" in reason for reason in verdict.reasons)
    assert TAG_TRUNCATED in verdict.tags


def test_evaluate_keeps_translation_that_ends_with_punctuation() -> None:
    verdict = evaluate_segment_confidence(
        "김진우는 몇 번째인지 모를 남자의 정액을 뒤로 받으며 절정에 달했다.",
        "就这样，在地铁男厕所里，金镇宇被不知第几个男人的精液灌满了后面，达到了高潮。",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert not verdict.is_low_confidence
    assert "truncated" not in " ".join(verdict.reasons)


def test_evaluate_flags_truncated_japanese_translation() -> None:
    verdict = evaluate_segment_confidence(
        "彼は部屋に入った。",
        "彼は部屋に入",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.JAPANESE,
        target_language=Language.JAPANESE,
    )

    assert verdict.is_low_confidence
    assert TAG_TRUNCATED in verdict.tags


def test_evaluate_flags_sentence_level_truncation_with_clean_ending() -> None:
    """Model dropped sentences but added a period to look complete."""

    verdict = evaluate_segment_confidence(
        "남자의 다리 사이로 두툼한 허벅지가 들어왔다. "
        "무릎이 바지 안에 갇힌 불알을 꾹 짓눌렀다. "
        "참을 수 없는 통증에 남자가 어깨를 흔들며 씩씩거렸다. "
        "신음 안 내? 이강이 귓가에 낮게 속삭이더니, "
        "이번엔 바지 지퍼를 내리고 자지를 꽉 움켜쥐었다.",
        "男人的双腿之间，挤进了粗壮的大腿。膝盖隔着裤子，狠狠压住了被夹住的睾丸。",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_TRUNCATED in verdict.tags
    assert any("sentence-ending" in r for r in verdict.reasons)


def test_evaluate_does_not_flag_truncation_when_source_ends_without_punctuation() -> None:
    verdict = evaluate_segment_confidence(
        "제1장 뒤로 가는 알파",
        "第1章 倒退的阿尔法",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert not verdict.is_low_confidence
    assert "truncated" not in " ".join(verdict.reasons)


def test_evaluate_flags_korean_residue_when_source_is_korean() -> None:
    verdict = evaluate_segment_confidence(
        "신해범이 방에 들어왔다.",
        "신해범 walked into the room.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.KOREAN,
    )

    assert verdict.is_low_confidence
    assert any("Korean residue" in reason for reason in verdict.reasons)
    assert TAG_SOURCE_RESIDUE in verdict.tags


def test_evaluate_flags_japanese_kana_residue_when_source_is_japanese() -> None:
    verdict = evaluate_segment_confidence(
        "彼は部屋に入った。",
        "彼は entered the room.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.JAPANESE,
    )

    assert verdict.is_low_confidence
    assert any("Japanese kana residue" in reason for reason in verdict.reasons)


def test_evaluate_keeps_korean_punctuation_limit_unchanged() -> None:
    verdict = evaluate_segment_confidence(
        "문장," * 20,
        "译文，" * 5,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=12,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_PUNCTUATION_ANOMALY in verdict.tags


def test_evaluate_scales_punctuation_limit_for_latin_source_to_chinese() -> None:
    verdict = evaluate_segment_confidence(
        "clause," * 20,
        "译文，" * 5,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=12,
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_PUNCTUATION_ANOMALY not in verdict.tags


@pytest.mark.parametrize(
    ("source_language", "source", "translated"),
    [
        (
            Language.ENGLISH,
            "He knew this should never have happened before they arrived.",
            "他知道 this should never have happened before，但为时已晚。",
        ),
        (
            Language.FRENCH,
            "Il savait que cette histoire ne pouvait pas continuer ainsi.",
            "他知道 cette histoire ne pouvait pas continuer ainsi，必须结束了。",
        ),
    ],
)
def test_evaluate_flags_shared_latin_source_phrase_in_chinese(
    source_language: Language,
    source: str,
    translated: str,
) -> None:
    verdict = evaluate_segment_confidence(
        source,
        translated,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=source_language,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_SOURCE_RESIDUE in verdict.tags


def test_evaluate_flags_latin_source_phrase_in_traditional_chinese() -> None:
    verdict = evaluate_segment_confidence(
        "He knew this should never have happened before they arrived.",
        "他知道 this should never have happened before，但為時已晚。",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_TRADITIONAL,
    )

    assert TAG_SOURCE_RESIDUE in verdict.tags


def test_evaluate_normalizes_decomposed_latin_source_phrase() -> None:
    source = unicodedata.normalize(
        "NFD",
        "Tôi biết câu chuyện này không thể tiếp tục như vậy.",
    )
    translated = unicodedata.normalize(
        "NFD",
        "他说 câu chuyện này không thể tiếp tục như vậy，必须结束。",
    )
    verdict = evaluate_segment_confidence(
        source,
        translated,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.VIETNAMESE,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_SOURCE_RESIDUE in verdict.tags


def test_evaluate_allows_latin_name_and_title_in_chinese_translation() -> None:
    verdict = evaluate_segment_confidence(
        "Alice returned to New York after reading Five by Five.",
        "Alice读完《Five by Five》后回到了纽约。",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_SOURCE_RESIDUE not in verdict.tags


def test_evaluate_allows_unchanged_short_camelcase_product_name() -> None:
    verdict = evaluate_segment_confidence(
        "iPhone 15 Pro Max",
        "iPhone 15 Pro Max",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert not verdict.is_low_confidence


def test_evaluate_flags_unchanged_generic_english_title() -> None:
    verdict = evaluate_segment_confidence(
        "The Exiled Queen",
        "The Exiled Queen",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.ENGLISH,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_SOURCE_RESIDUE in verdict.tags


@pytest.mark.parametrize(
    ("source_language", "source", "translated", "reason_fragment"),
    [
        (
            Language.RUSSIAN,
            "Он медленно вошёл в комнату и закрыл за собой дверь.",
            "Он медленно вошёл в комнату и закрыл за собой дверь.",
            "Cyrillic",
        ),
        (
            Language.ARABIC,
            "دخل الغرفة ببطء ثم أغلق الباب خلفه.",
            "دخل الغرفة ببطء ثم أغلق الباب خلفه.",
            "Arabic",
        ),
    ],
)
def test_evaluate_flags_conservative_script_residue_for_chinese_target(
    source_language: Language,
    source: str,
    translated: str,
    reason_fragment: str,
) -> None:
    verdict = evaluate_segment_confidence(
        source,
        translated,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=source_language,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_SOURCE_RESIDUE in verdict.tags
    assert any(reason_fragment in reason for reason in verdict.reasons)


@pytest.mark.parametrize(
    ("source_language", "source", "translated"),
    [
        (
            Language.RUSSIAN,
            "Он встретил Анну у вокзала.",
            "他在车站遇见了Анна。",
        ),
        (
            Language.ARABIC,
            "قابل ليلى عند المحطة.",
            "他在车站遇见了ليلى。",
        ),
    ],
)
def test_evaluate_allows_short_preserved_names_from_non_latin_scripts(
    source_language: Language,
    source: str,
    translated: str,
) -> None:
    verdict = evaluate_segment_confidence(
        source,
        translated,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=source_language,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_SOURCE_RESIDUE not in verdict.tags


def test_evaluate_does_not_apply_new_script_residue_to_non_chinese_target() -> None:
    verdict = evaluate_segment_confidence(
        "Он медленно вошёл в комнату.",
        "He entered the room with the name Анна beside him.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.RUSSIAN,
        target_language=Language.ENGLISH,
    )

    assert TAG_SOURCE_RESIDUE not in verdict.tags


def test_evaluate_flags_identical_source_and_translation() -> None:
    verdict = evaluate_segment_confidence(
        "This should not come back unchanged.",
        "This should not come back unchanged.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.ENGLISH,
    )

    assert verdict.is_low_confidence
    assert any("too similar" in reason for reason in verdict.reasons)
    assert TAG_VERBATIM_ECHO in verdict.tags


def test_evaluate_flags_english_function_word_leak_in_chinese_target() -> None:
    verdict = evaluate_segment_confidence(
        "그는 방 안으로 걸어 들어갔다.",
        "He walked into the room and she looked at him.",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_FUNCTION_WORD_RESIDUE in verdict.tags
    assert TAG_TARGET_LANGUAGE_WEAK in verdict.tags


def test_evaluate_flags_single_english_function_word_in_chinese_prose() -> None:
    verdict = evaluate_segment_confidence(
        "에이블이 미움을 받지 않은 건 그도 벌에게 쏘였다는 점 때문이었다.",
        "艾布尔之所以没被大家怨恨，是因为他自己也被蛰得浑身是伤。士兵们 and 乌修勒看着再次走向森林的艾布尔。",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_FUNCTION_WORD_RESIDUE in verdict.tags


def test_evaluate_allows_latin_proper_name_in_chinese_target() -> None:
    verdict = evaluate_segment_confidence(
        "레가스 2권",
        "《雷加斯(Regas)》第2卷",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert TAG_FUNCTION_WORD_RESIDUE not in verdict.tags


def test_evaluate_allows_latin_title_when_marker_is_translated() -> None:
    verdict = evaluate_segment_confidence(
        "Five by Five (5x5) 프롤로그",
        "Five by Five (5x5) 序",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert not verdict.is_low_confidence


def test_evaluate_allows_unchanged_short_latin_title_without_target_content() -> None:
    verdict = evaluate_segment_confidence(
        "Five by Five (5x5)",
        "Five by Five (5x5)",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert not verdict.is_low_confidence


def test_evaluate_flags_ordinary_unchanged_latin_sentence() -> None:
    verdict = evaluate_segment_confidence(
        "this should not come back unchanged",
        "this should not come back unchanged",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_VERBATIM_ECHO in verdict.tags


def test_evaluate_flags_unchanged_korean_soft_symbol_line_without_target_content() -> None:
    verdict = evaluate_segment_confidence(
        "ㅋㅋㅋㅋ!!",
        "ㅋㅋㅋㅋ!!",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_SOURCE_RESIDUE in verdict.tags


def test_evaluate_still_flags_unchanged_korean_sentence() -> None:
    verdict = evaluate_segment_confidence(
        "안녕하세요 친구입니다",
        "안녕하세요 친구입니다",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_SOURCE_RESIDUE in verdict.tags


def test_evaluate_flags_model_chatter_wrapper() -> None:
    verdict = evaluate_segment_confidence(
        "그는 고개를 끄덕였다.",
        "译文：他点了点头。",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    assert verdict.is_low_confidence
    assert TAG_MODEL_CHATTER in verdict.tags


def test_evaluate_model_anomaly_golden_fixtures() -> None:
    cases = json.loads(
        (CONFIDENCE_FIXTURE_DIR / "model_anomalies.json").read_text(
            encoding="utf-8"
        )
    )
    for case in cases:
        verdict = evaluate_segment_confidence(
            case["source"],
            case["translation"],
            min_length_ratio=0.0,
            max_length_ratio=10.0,
            max_punctuation_delta=20,
            source_language=Language(case["source_language"]),
            target_language=Language(case["target_language"]),
        )
        expected_tags = set(case["tags"])
        assert expected_tags <= set(verdict.tags), case["name"]
        assert verdict.is_low_confidence is bool(expected_tags), case["name"]


def test_evaluate_allows_unchanged_isbn_identifier() -> None:
    verdict = evaluate_segment_confidence(
        "ISBN | 979-11-01-87478-2",
        "ISBN | 979-11-01-87478-2",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.KOREAN,
    )

    assert not verdict.is_low_confidence


def test_evaluate_allows_unchanged_masked_phone_identifier() -> None:
    verdict = evaluate_segment_confidence(
        "010-xxxx-xxxx",
        "010-xxxx-xxxx",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=4,
        source_language=Language.KOREAN,
    )

    assert not verdict.is_low_confidence


def test_evaluate_allows_unchanged_online_identifiers() -> None:
    for identifier in (
        "www.ebookclub.co.kr",
        "https://example.com/path?q=1#section",
        "@Ventura_official_f1",
        "@dylanroxburgh",
    ):
        verdict = evaluate_segment_confidence(
            identifier,
            identifier,
            min_length_ratio=0.0,
            max_length_ratio=10.0,
            max_punctuation_delta=4,
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
        )

        assert not verdict.is_low_confidence


@dataclass
class TruncatingTransport:
    """Returns a translation that's clearly too short to pass length check."""

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
            for key, _value in parsed.items():
                # Always return the same single character — way below the
                # configured min_length_ratio so it's flagged.
                lines.append(json.dumps({key: "x"}, ensure_ascii=False))
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return TransportResult(200, body)


def test_orchestrator_records_low_confidence_segments_in_statistics(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()

    (input_dir / "Sample.txt").write_text(
        "This is a meaningfully long source line.\n"
        "Another reasonably long source line!\n",
        encoding="utf-8",
    )

    transport = TruncatingTransport()
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-conf",
    )
    config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="model-x",
            api_keys=("key",),
            thinking_level=ThinkingLevel.OFF,
            rpm_limit=0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        chunk_size=4,
        context_line_count=0,
        enable_confidence_check=True,
        min_length_ratio=0.3,
        max_length_ratio=3.0,
        max_punctuation_delta=4,
    )

    result = asyncio.run(orchestrator.run(config))

    assert result.final_status is TaskStatus.COMPLETED
    assert len(result.statistics.low_confidence_segments) == 2
    stats = json.loads(result.statistics_path.read_text(encoding="utf-8"))
    assert len(stats["low_confidence_segments"]) == 2
    for record in stats["low_confidence_segments"]:
        assert record["segment_id"]
        assert record["reasons"]


def test_orchestrator_does_not_record_when_confidence_check_disabled(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Sample.txt").write_text(
        "Long meaningful source line.\n", encoding="utf-8"
    )

    transport = TruncatingTransport()
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=LlmClient(transport=transport),
        clock=lambda: "2026-04-27T00:00:00+00:00",
        id_factory=lambda: "task-noconf",
    )
    config = TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.ENGLISH,
        target_language=Language.KOREAN,
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example/api/v1/",
            model_id="model-x",
            api_keys=("key",),
            rpm_limit=0,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
        glossary=Glossary.empty(),
        chunk_size=4,
        context_line_count=0,
        enable_confidence_check=False,
    )

    result = asyncio.run(orchestrator.run(config))

    assert result.statistics.low_confidence_segments == ()


def test_evaluate_flags_pure_jamo_residue_without_cjk_context() -> None:
    """Saturated Compat-Jamo output with NO CJK content = real
    laziness (model just echoed Korean chat slang). Catch it."""

    verdict = evaluate_segment_confidence(
        "사진 봐 ㅋㅋㅋ ㅠㅠ",
        "ㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅠㅠ",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
    )

    assert verdict.is_low_confidence
    assert any("Korean residue" in r for r in verdict.reasons)


def test_evaluate_allows_jamo_emoji_fragments_alongside_chinese() -> None:
    """Mixed output — Chinese prose + jamo fragments (ㅋㅋ / ㅠㅠ) — is
    legitimate translator retention of chat-style emoji. Don't flag
    when CJK ideographs back the line as a real translation attempt."""

    verdict = evaluate_segment_confidence(
        "ㅆㅣ바류 ㅠㅠ 이 사진 볼수록 니랑 개똑같음 ㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋ",
        "靠北啊 ㅠㅠ 这张照片越看越跟你一模一样 ㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋ",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
    )

    assert not any("Korean residue" in r for r in verdict.reasons)


def test_evaluate_allows_single_korean_letter_cultural_reference() -> None:
    """Sorting marker "ㄱ" or shape descriptor "ㄷ字形" is intentional
    cultural retention — single Compat-Jamo letter in mostly-Chinese
    text must not be flagged."""

    long_with_ref = (
        "他开始一张一张分发带来的那叠纸。按姓氏顺序，从ㄱ开始。姜在九，景元泰，然后。"
    )
    verdict = evaluate_segment_confidence(
        "그는 종이를 한 장씩 나눠 주기 시작했다. 성씨 순으로 ㄱ부터.",
        long_with_ref,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
    )

    assert not any("Korean residue" in r for r in verdict.reasons)


@pytest.mark.parametrize("translated", ["忙ㅏㅏㅇ死了", "小ㅐㅐㅐ鱼干"])
def test_evaluate_flags_non_emoticon_jamo_inside_chinese(translated: str) -> None:
    verdict = evaluate_segment_confidence(
        "한국어 문장",
        translated,
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
    )

    assert verdict.is_low_confidence
    assert any("Korean residue" in reason for reason in verdict.reasons)


def test_evaluate_flags_korean_halfwidth_hangul() -> None:
    """Halfwidth Hangul (U+FFA0-U+FFDC) is legacy game-text leakage
    and should be flagged even at low ratio because it's never used
    in normal Chinese prose."""

    # ﾠ + ﾡ is halfwidth filler + halfwidth ㄱ
    verdict = evaluate_segment_confidence(
        "안녕하세요",
        "你好ﾡﾢﾣﾤﾥ",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.KOREAN,
    )

    assert verdict.is_low_confidence
    assert any("Korean residue" in r for r in verdict.reasons)


def test_evaluate_flags_japanese_halfwidth_katakana() -> None:
    """Halfwidth katakana (U+FF65-U+FF9F) shows up in legacy game text
    and must be detected like full-width kana."""

    verdict = evaluate_segment_confidence(
        "おはようございます",
        "早上好ｦｧｨｩ",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.JAPANESE,
    )

    assert verdict.is_low_confidence
    assert any("Japanese kana residue" in r for r in verdict.reasons)


def test_evaluate_flags_single_hiragana_particle_leak() -> None:
    """A single hiragana particle (は, の, etc.) leaking into otherwise-
    Chinese text is real laziness — Japanese kana have no cultural
    retention pattern in Chinese translation."""

    verdict = evaluate_segment_confidence(
        "彼は部屋に入った。",
        "彼は走进了房间。",
        min_length_ratio=0.0,
        max_length_ratio=10.0,
        max_punctuation_delta=20,
        source_language=Language.JAPANESE,
    )

    assert verdict.is_low_confidence
    assert any("Japanese kana residue" in r for r in verdict.reasons)


def test_evaluate_does_not_flag_japanese_punctuation_chars_in_chinese() -> None:
    """ー (long-sound mark), ・ (middle dot), ゛ ゜ (dakuten/handakuten)
    and the halfwidth equivalents are Japanese-origin punctuation that
    legitimately appear in transliterated names and Chinese loanwords.
    They must NOT trigger Japanese kana residue when no actual kana
    letter is present."""

    for translated in (
        "哈利・波特来了。",         # fullwidth middle dot ・
        "杰ー夫ー来了。",            # fullwidth long-sound mark ー only
        "音乐有 ゛ ゜ 标记。",       # dakuten / handakuten only
    ):
        verdict = evaluate_segment_confidence(
            "「ジェフ」と言った。",
            translated,
            min_length_ratio=0.0,
            max_length_ratio=10.0,
            max_punctuation_delta=20,
            source_language=Language.JAPANESE,
        )
        assert not any(
            "Japanese kana residue" in r for r in verdict.reasons
        ), (translated, verdict.reasons)
