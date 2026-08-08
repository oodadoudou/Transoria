"""Tests for ``transoria.bridge.handlers.proofreading``.

Exercises the load → edit → regenerate loop using a real on-disk cache
seeded by ``TaskCache.write_seed`` and a real ``TaskService``. The
LLM is never called — proofreading is a pure cache-edit + writer
pipeline, so no transport stub is needed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers.proofreading import register
from transoria.bridge.router import BridgeRouter
from transoria.bridge.task_registry import TaskRegistry
from transoria.bridge.task_service import TaskService
from transoria.domain import (
    Language,
    SubtaskStatus,
    TaskKind,
    TaskStatus,
)
from transoria.llm.client import LlmClient
from transoria.model_profiles import ModelProfileStore
from transoria.runtime.cache import TaskCache
from transoria.runtime.subtask import Subtask
from transoria.runtime.task_record import TaskRecord
from transoria.settings import SettingsStore
from transoria.workflows.translation.confidence import (
    TAG_FUNCTION_WORD_RESIDUE,
    TAG_TRUNCATED,
)


def _make_service(tmp_path: Path) -> TaskService:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    return TaskService(
        cache=TaskCache(root=cache_root / "tasks"),
        registry=TaskRegistry(),
        settings_store=SettingsStore(path=cache_root / "settings.json"),
        profile_store=ModelProfileStore.from_cache_root(cache_root),
        prompts_cache_root=cache_root,
        llm_client_factory=lambda: LlmClient(transport=None),
    )


def _seed_translation_task(
    service: TaskService,
    *,
    task_id: str,
    input_dir: Path,
    output_dir: Path,
    file_segments: list[tuple[str, str, str]],  # (segment_id, src, dst)
    status: TaskStatus = TaskStatus.COMPLETED,
    possible_duplicate_ids: set[str] | None = None,
    metadata_overrides: dict[str, object] | None = None,
) -> None:
    """Plant a fully-completed translation task in the cache.

    ``file_segments`` is ``[(segment_id, src, dst), ...]`` — all segments
    land in a single subtask for simplicity.
    """

    metadata: dict[str, object] = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "source_language": Language.KOREAN.value,
        "target_language": Language.CHINESE_SIMPLIFIED.value,
        "model_id": "test-model",
        "prompt_preset_id": "default-translation-en",
    }
    if metadata_overrides:
        metadata.update(metadata_overrides)

    record = TaskRecord(
        id=task_id,
        kind=TaskKind.TRANSLATION,
        status=status,
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:01:00+00:00",
        metadata=metadata,
    )
    request_payload = {
        "version": 1,
        "segments": [
            {
                "segment_id": seg_id,
                "chunk_index": idx,
                "prompt_text": src,
                "original_text": src,
                "protection_spans": [],
                "leading_whitespace": "",
                "trailing_whitespace": "",
            }
            for idx, (seg_id, src, _dst) in enumerate(file_segments)
        ],
        "context_lines": [],
        "glossary_entries": [],
    }
    import json as _json

    possible_duplicate_ids = possible_duplicate_ids or set()
    response_payload = {
        "version": 2,
        "translations": {seg_id: dst for seg_id, _src, dst in file_segments},
        "low_confidence": [
            {
                "segment_id": seg_id,
                "reasons": ["duplicate_drift_after_low_confidence_retry"],
                "tags": ["possible_duplicate"],
            }
            for seg_id in possible_duplicate_ids
        ],
    }
    subtask = Subtask(
        id="chunk-00000",
        task_id=task_id,
        status=SubtaskStatus.COMPLETED,
        request_payload=request_payload,
        response_content=_json.dumps(response_payload, ensure_ascii=False),
    )
    service.cache.write_seed(record, (subtask,))


@pytest.fixture
def router_and_service(tmp_path: Path):
    service = _make_service(tmp_path)
    router = BridgeRouter()
    register(router, service=service)
    return router, service, tmp_path


# list_tasks


def test_list_tasks_returns_translation_tasks(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-1",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好")],
    )

    response = router.call("proofreading.list_tasks", {})
    assert len(response["tasks"]) == 1
    assert response["tasks"][0]["id"] == "translation-pf-1"


def test_list_tasks_includes_failed_and_stopped_translation_tasks(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    for task_id, status in (
        ("translation-failed", TaskStatus.FAILED),
        ("translation-stopped", TaskStatus.STOPPED),
    ):
        _seed_translation_task(
            service,
            task_id=task_id,
            input_dir=tmp_path / f"in-{task_id}",
            output_dir=tmp_path / f"out-{task_id}",
            file_segments=[("0:0", "안녕", "你好")],
            status=status,
        )

    response = router.call("proofreading.list_tasks", {})
    ids = {task["id"] for task in response["tasks"]}
    assert {"translation-failed", "translation-stopped"} <= ids


def test_list_tasks_skips_runs_with_no_progress(router_and_service):
    router, service, tmp_path = router_and_service
    # A task in cache with 0 segments — orchestrator never seeded any
    # subtasks. proofreading should hide it.
    record = TaskRecord(
        id="translation-empty",
        kind=TaskKind.TRANSLATION,
        status=TaskStatus.FAILED,
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:01+00:00",
        metadata={},
    )
    service.cache.write_seed(record, ())

    response = router.call("proofreading.list_tasks", {})
    assert response["tasks"] == []


# load_snapshot


def test_load_snapshot_returns_segments_sorted_by_file_then_index(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-2",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        # Out-of-order on purpose: handler must sort.
        file_segments=[
            ("1:0", "안녕", "你好"),
            ("0:5", "산", "山"),
            ("0:0", "꽃", "花"),
        ],
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-2"}
    )
    assert response["task_id"] == "translation-pf-2"
    assert [item["segment_id"] for item in response["items"]] == [
        "0:0",
        "0:5",
        "1:0",
    ]
    assert response["items"][0]["src"] == "꽃"
    assert response["items"][0]["dst"] == "花"
    assert response["items"][0]["subtask_ids"] == ["chunk-00000"]


def test_load_snapshot_reports_all_source_subtasks(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-subtasks",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好")],
    )
    child = Subtask(
        id="chunk-00000.s1",
        task_id="translation-pf-subtasks",
        status=SubtaskStatus.COMPLETED,
        request_payload={
            "version": 1,
            "segments": [
                {
                    "segment_id": "0:0",
                    "chunk_index": 0,
                    "prompt_text": "안녕",
                    "original_text": "안녕",
                    "protection_spans": [],
                    "leading_whitespace": "",
                    "trailing_whitespace": "",
                }
            ],
            "context_lines": [],
            "glossary_entries": [],
        },
        response_content=json.dumps(
            {"version": 2, "translations": {"0:0": "你好"}, "low_confidence": []},
            ensure_ascii=False,
        ),
    )
    service.cache.save_subtask(child)

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-subtasks"}
    )

    assert response["items"][0]["subtask_ids"] == ["chunk-00000", "chunk-00000.s1"]


def test_load_snapshot_uses_latest_response_for_low_confidence_metadata(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-latest-confidence",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "안녕")],
    )
    first = service.cache.load_subtasks("translation-pf-latest-confidence")[0]
    first_payload = json.loads(first.response_content or "{}")
    first_payload["low_confidence"] = [
        {
            "segment_id": "0:0",
            "reasons": ["Korean residue remains in translation"],
            "tags": ["source_residue"],
        }
    ]
    service.cache.save_subtask(
        replace(first, response_content=json.dumps(first_payload, ensure_ascii=False))
    )
    service.cache.save_subtask(
        Subtask(
            id="chunk-00000.s1",
            task_id="translation-pf-latest-confidence",
            status=SubtaskStatus.COMPLETED,
            request_payload=first.request_payload,
            response_content=json.dumps(
                {"version": 2, "translations": {"0:0": "你好"}, "low_confidence": []},
                ensure_ascii=False,
            ),
        )
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-latest-confidence"},
    )

    item = response["items"][0]
    assert item["dst"] == "你好"
    assert item["low_confidence"] is False
    assert "tags" not in item
    assert "reasons" not in item


def test_load_snapshot_ignores_stale_failed_response_payload(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-ignore-failed-response",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好")],
    )
    completed = service.cache.load_subtasks("translation-pf-ignore-failed-response")[0]
    service.cache.save_subtask(
        Subtask(
            id="chunk-99999",
            task_id="translation-pf-ignore-failed-response",
            status=SubtaskStatus.FAILED,
            request_payload=completed.request_payload,
            response_content=json.dumps(
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
                ensure_ascii=False,
            ),
        )
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-ignore-failed-response"},
    )

    item = response["items"][0]
    assert item["dst"] == "你好"
    assert item["low_confidence"] is False
    assert "tags" not in item
    assert "reasons" not in item


def test_load_snapshot_reads_legacy_flat_translation_response(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-legacy-flat",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "안녕")],
    )
    subtask = service.cache.load_subtasks("translation-pf-legacy-flat")[0]
    service.cache.save_subtask(
        replace(
            subtask,
            response_content=json.dumps({"0:0": "你好"}, ensure_ascii=False),
        )
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-legacy-flat"}
    )

    item = response["items"][0]
    assert item["dst"] == "你好"
    assert item["low_confidence"] is False


def test_load_snapshot_refreshes_model_anomaly_tags_for_cached_translation(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-dynamic-confidence",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            (
                "0:0",
                "에이블이 미움을 받지 않은 건 그도 벌에게 쏘였다는 점 때문이었다.",
                "艾布尔之所以没被大家怨恨，是因为他自己也被蛰得浑身是伤。士兵们 and 乌修勒看着再次走向森林的艾布尔。",
            )
        ],
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-dynamic-confidence"},
    )

    item = response["items"][0]
    assert item["low_confidence"] is True
    assert TAG_FUNCTION_WORD_RESIDUE in item["tags"]
    assert (
        "English function-word residue remains in Chinese translation"
        in item["reasons"]
    )


def test_load_snapshot_tags_truncated_cached_translation(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-truncated",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            (
                "0:0",
                "들려오는 말에 진우가 다급히 고개를 가로저었지만 상대는 전혀 들어먹질 않았다.",
                "听到这句话，镇宇慌忙摇头，但对方根本充耳不闻，反而更快地将阴茎往里面猛插。情急之下，镇宇掰",
            )
        ],
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-truncated"},
    )

    item = response["items"][0]
    assert item["low_confidence"] is True
    assert TAG_TRUNCATED in item["tags"]
    assert any("truncated" in reason for reason in item["reasons"])


def test_load_snapshot_keeps_similar_source_and_translation_unflagged(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-similar-pair",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            (
                "0:0",
                "이정의 순종적인 태도에 남자는 기꺼운 심정을 숨기지 않았다.",
                "李正不敢与男人对视，低下了头。",
            ),
            (
                "0:1",
                "이정은 남자와 시선을 감히 마주하지 못하고 고개를 숙였다.",
                "李正不敢与那男人对视，低下了头。",
            ),
        ],
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-similar-pair"}
    )

    assert [item["segment_id"] for item in response["items"]] == ["0:0", "0:1"]
    for item in response["items"]:
        assert item["low_confidence"] is False
        assert "possible_duplicate" not in item.get("tags", [])


def test_load_snapshot_tags_possible_adjacent_duplicate(router_and_service):
    router, service, tmp_path = router_and_service
    duplicate = (
        "李江贤想起来，当时自己心里不爽，便口无遮拦地乱说，结果挨了陈熙沫一耳光。"
    )
    _seed_translation_task(
        service,
        task_id="translation-pf-dup",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            ("0:0", "첫 번째 문장은 이전 대화를 회상했다.", duplicate),
            (
                "0:1",
                "두 번째 문장은 전혀 다른 사건을 설명했다.",
                duplicate,
            ),
        ],
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-dup"}
    )

    assert [item["segment_id"] for item in response["items"]] == ["0:0", "0:1"]
    for item in response["items"]:
        assert item["low_confidence"] is False
        assert "possible_duplicate" in item["tags"]
        assert "adjacent_translation_possible_duplicate" in item["reasons"]


def test_load_snapshot_tags_glossary_not_applied(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-glossary-missing",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "신해범이 돌아왔다.", "海范回来了。")],
        metadata_overrides={
            "glossary": [
                {
                    "src": "신해범",
                    "dst": "申海范",
                    "info": "character",
                    "enabled": True,
                }
            ]
        },
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-glossary-missing"},
    )

    item = response["items"][0]
    assert "glossary_not_applied" in item["tags"]
    assert "glossary_term_target_missing" in item["reasons"]
    assert item["glossary_terms"] == [
        {
            "src": "신해범",
            "dst": "申海范",
            "info": "character",
            "applied": False,
            "inconsistent": False,
        }
    ]


def test_load_snapshot_does_not_infer_term_inconsistency_from_missing_canonical_hits(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-term-inconsistency",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            ("0:0", "신해범이 돌아왔다.", "申海范回来了。"),
            ("0:1", "신해범은 웃었다.", "海范笑了。"),
        ],
        metadata_overrides={
            "glossary": [
                {
                    "src": "신해범",
                    "dst": "申海范",
                    "info": "character",
                    "enabled": True,
                }
            ]
        },
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-term-inconsistency"},
    )
    items = {item["segment_id"]: item for item in response["items"]}

    assert "term_inconsistency" not in items["0:0"].get("tags", [])
    assert "glossary_not_applied" not in items["0:0"].get("tags", [])
    assert items["0:0"]["glossary_terms"][0]["applied"] is True
    assert items["0:0"]["glossary_terms"][0]["inconsistent"] is False
    assert "term_inconsistency" not in items["0:1"].get("tags", [])
    assert "glossary_not_applied" in items["0:1"]["tags"]
    assert "glossary_term_translation_inconsistent" not in items["0:1"].get(
        "reasons", []
    )
    assert items["0:1"]["glossary_terms"][0]["applied"] is False
    assert items["0:1"]["glossary_terms"][0]["inconsistent"] is False


def test_load_snapshot_tags_short_possible_duplicate(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-short-dup",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            ("0:0", "왜. 아쉽냐?", "现在应该在睡觉吧。"),
            ("0:1", "축구를 보러 갔다.", "现在应该在睡觉吧。"),
        ],
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-short-dup"}
    )

    for item in response["items"]:
        assert item["low_confidence"] is False
        assert "possible_duplicate" in item["tags"]


def test_load_snapshot_keeps_similar_short_dialogue_unflagged(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-short-similar-dialogue",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            ("0:0", "“혼… 났어요.”", "“……被教训了。”"),
            ("0:1", "“혼났다고?”", "“被教训了？”"),
        ],
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-short-similar-dialogue"},
    )

    for item in response["items"]:
        assert item["low_confidence"] is False
        assert "possible_duplicate" not in item.get("tags", [])


def test_load_snapshot_tags_nearby_partial_duplicate(router_and_service):
    router, service, tmp_path = router_and_service
    repeated = "几个同期纷纷把酒递过去，安抚着金智云。"
    _seed_translation_task(
        service,
        task_id="translation-pf-partial-dup",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            ("0:0", "첫 번째 문장은 전혀 다른 내용이었다.", repeated),
            ("0:1", "중간에 짧은 대사가 끼어 있었다.", "“现在应该在睡觉吧。”"),
            (
                "0:2",
                "두 번째 문장은 다른 사건을 설명했다.",
                f"{repeated} 但看得出来，他们对陈熙沫也颇为不满。",
            ),
        ],
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-partial-dup"}
    )
    items = {item["segment_id"]: item for item in response["items"]}

    assert items["0:0"]["low_confidence"] is False
    assert items["0:2"]["low_confidence"] is False
    assert "possible_duplicate" in items["0:0"]["tags"]
    assert "possible_duplicate" in items["0:2"]["tags"]
    assert items["0:1"]["low_confidence"] is False


def test_load_snapshot_extends_existing_duplicate_tag_to_partner(router_and_service):
    router, service, tmp_path = router_and_service
    left = "不，你现在什么都不知道。趁我好说话，赶紧回家。我说了这里是釜山。"
    right = "不，你现在什么都不懂。趁我好声好气的时候赶紧回家。我说了这里是釜山。"
    _seed_translation_task(
        service,
        task_id="translation-pf-existing-dup-partner",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            ("0:0", "부산인 거 알아요. 집 주소 보내요.", left),
            ("0:1", "그가 더는 말하지 않고 침묵했다.", right),
        ],
        possible_duplicate_ids={"0:0"},
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-existing-dup-partner"},
    )
    items = {item["segment_id"]: item for item in response["items"]}

    assert items["0:0"]["low_confidence"] is True
    assert items["0:1"]["low_confidence"] is False
    assert "possible_duplicate" in items["0:1"]["tags"]


def test_load_snapshot_keeps_legitimate_repeated_source_unflagged(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-legit-repeat",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[
            ("0:0", "지금 자고 있겠지.", "现在应该在睡觉吧。"),
            ("0:1", "지금 자고 있겠지.", "现在应该在睡觉吧。"),
        ],
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-legit-repeat"}
    )

    for item in response["items"]:
        assert item["low_confidence"] is False
        assert "possible_duplicate" not in item.get("tags", [])


def test_load_snapshot_rejects_missing_task(router_and_service):
    router, _service, _tmp = router_and_service
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.load_snapshot",
            {"task_id": "translation-does-not-exist"},
        )
    assert caught.value.code == "bridge.not_found"


def test_load_snapshot_rejects_non_translation_task(router_and_service):
    router, service, _tmp = router_and_service
    record = TaskRecord(
        id="glossary-foo",
        kind=TaskKind.GLOSSARY,
        status=TaskStatus.COMPLETED,
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:01:00+00:00",
        metadata={},
    )
    service.cache.write_seed(record, ())
    with pytest.raises(BridgeError) as caught:
        router.call("proofreading.load_snapshot", {"task_id": "glossary-foo"})
    assert caught.value.code == "bridge.invalid_argument"


def test_load_snapshot_rejects_running_translation_task(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-running",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好")],
        status=TaskStatus.RUNNING,
    )

    with pytest.raises(BridgeError) as caught:
        router.call("proofreading.load_snapshot", {"task_id": "translation-running"})

    assert caught.value.code == "bridge.conflict"
    assert caught.value.payload.details["status"] == "running"


# update_segment


def test_update_segment_persists_edit_to_cache(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-3",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好"), ("0:1", "산", "山")],
    )

    response = router.call(
        "proofreading.update_segment",
        {"task_id": "translation-pf-3", "segment_id": "0:0", "dst": "嗨"},
    )
    assert response["updated"] is True
    assert response["dst"] == "嗨"
    assert response["low_confidence"] is False
    assert response["tags"] == []
    assert response["reasons"] == []

    # Round-trip: load_snapshot reflects the edit.
    after = router.call("proofreading.load_snapshot", {"task_id": "translation-pf-3"})
    items = {item["segment_id"]: item for item in after["items"]}
    assert items["0:0"]["dst"] == "嗨"
    assert items["0:1"]["dst"] == "山"


def test_update_segment_refreshes_low_confidence_tags(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-low-conf",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "휴지가 왔다", "휴지가 왔다")],
    )
    subtask = service.cache.load_subtasks("translation-pf-low-conf")[0]
    payload = json.loads(subtask.response_content or "{}")
    payload["low_confidence"] = [
        {
            "segment_id": "0:0",
            "reasons": ["Korean residue remains in translation"],
            "tags": ["source_residue"],
        }
    ]
    service.cache.save_subtask(
        replace(subtask, response_content=json.dumps(payload, ensure_ascii=False))
    )

    response = router.call(
        "proofreading.update_segment",
        {
            "task_id": "translation-pf-low-conf",
            "segment_id": "0:0",
            "dst": "休止来了",
        },
    )
    assert response["low_confidence"] is False
    assert response["tags"] == []
    assert response["reasons"] == []
    after = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-low-conf"}
    )
    assert after["items"][0]["low_confidence"] is False
    assert "tags" not in after["items"][0]

    response = router.call(
        "proofreading.update_segment",
        {
            "task_id": "translation-pf-low-conf",
            "segment_id": "0:0",
            "dst": "휴지来了",
        },
    )
    assert response["low_confidence"] is True
    assert response["tags"] == ["source_residue"]
    assert any("Korean residue" in reason for reason in response["reasons"])
    after = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-low-conf"}
    )
    assert after["items"][0]["low_confidence"] is True
    assert after["items"][0]["tags"] == ["source_residue"]
    assert any("Korean residue" in reason for reason in after["items"][0]["reasons"])


def test_update_segment_rejects_unknown_segment(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-4",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好")],
    )
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.update_segment",
            {
                "task_id": "translation-pf-4",
                "segment_id": "9:9",
                "dst": "x",
            },
        )
    assert caught.value.code == "bridge.not_found"


def test_update_segment_rejects_non_string_dst(router_and_service):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-5",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好")],
    )
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.update_segment",
            {
                "task_id": "translation-pf-5",
                "segment_id": "0:0",
                "dst": 42,
            },
        )
    assert caught.value.code == "bridge.invalid_argument"


def test_load_snapshot_shows_source_fallback_for_missing_translation(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-missing",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好"), ("0:1", "산", "山")],
    )
    snapshot = service.cache.load("translation-pf-missing")
    subtask = snapshot.subtasks[0]
    payload = json.loads(subtask.response_content)
    del payload["translations"]["0:1"]
    service.cache.save_subtask(
        replace(
            subtask,
            response_content=json.dumps(payload, ensure_ascii=False),
        )
    )

    response = router.call(
        "proofreading.load_snapshot", {"task_id": "translation-pf-missing"}
    )

    item = response["items"][1]
    assert item["segment_id"] == "0:1"
    assert item["dst"] == "산"
    assert item["low_confidence"] is True
    assert item["tags"] == ["source_residue"]
    assert item["reasons"] == ["missing_translation_fell_back_to_source"]


def test_load_snapshot_keeps_marked_partial_rows_from_failed_subtask(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-partial-failed-response",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好"), ("0:1", "산", "산")],
        status=TaskStatus.FAILED,
    )
    subtask = service.cache.load_subtasks(
        "translation-pf-partial-failed-response"
    )[0]
    payload = {
        "version": 2,
        "translations": {"0:0": "你好", "0:1": "산"},
        "low_confidence": [
            {
                "segment_id": "0:1",
                "reasons": ["line_count_mismatch_after_max_retries"],
                "tags": ["source_residue"],
            }
        ],
        "accepted_overrides": ["0:0"],
    }
    service.cache.save_subtask(
        replace(
            subtask,
            status=SubtaskStatus.FAILED,
            response_content=json.dumps(payload, ensure_ascii=False),
        )
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-partial-failed-response"},
    )

    assert response["task_status"] == "failed"
    assert response["items"][0]["dst"] == "你好"
    assert response["items"][0]["low_confidence"] is False
    assert response["items"][1]["dst"] == "산"
    assert response["items"][1]["low_confidence"] is True
    assert response["items"][1]["tags"] == ["source_residue"]


def test_failed_task_load_snapshot_shows_missing_translation_fallback(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-failed-missing",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好"), ("0:1", "산", "山")],
        status=TaskStatus.FAILED,
    )
    snapshot = service.cache.load("translation-pf-failed-missing")
    subtask = snapshot.subtasks[0]
    payload = json.loads(subtask.response_content)
    del payload["translations"]["0:1"]
    service.cache.save_subtask(
        replace(
            subtask,
            response_content=json.dumps(payload, ensure_ascii=False),
        )
    )

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-failed-missing"},
    )

    item = response["items"][1]
    assert response["task_status"] == "failed"
    assert item["dst"] == "산"
    assert item["low_confidence"] is True
    assert item["tags"] == ["source_residue"]


def test_load_snapshot_ignores_unaccepted_failed_response_payload(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    _seed_translation_task(
        service,
        task_id="translation-pf-failed-stale",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        file_segments=[("0:0", "안녕", "你好")],
        status=TaskStatus.FAILED,
    )
    subtask = service.cache.load_subtasks("translation-pf-failed-stale")[0]
    service.cache.save_subtask(replace(subtask, status=SubtaskStatus.FAILED))

    response = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-failed-stale"},
    )

    item = response["items"][0]
    assert item["dst"] == "안녕"
    assert item["low_confidence"] is True
    assert item["tags"] == ["source_residue"]


# regenerate_outputs


def test_regenerate_outputs_writes_translated_txt_with_edited_dst(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    # The input file must exist so _scan_and_parse can re-read it.
    (input_dir / "sample.txt").write_text("안녕\n산\n", encoding="utf-8")

    # The orchestrator's preprocessor would assign segment_ids "0:0"
    # and "0:1" to those two non-empty lines. Plant matching cache.
    _seed_translation_task(
        service,
        task_id="translation-pf-regen",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("0:0", "안녕", "你好"), ("0:1", "산", "山")],
    )
    # Edit the second translation.
    router.call(
        "proofreading.update_segment",
        {
            "task_id": "translation-pf-regen",
            "segment_id": "0:1",
            "dst": "高山",
        },
    )

    response = router.call(
        "proofreading.regenerate_outputs",
        {"task_id": "translation-pf-regen"},
    )
    assert len(response["translated_files"]) == 1
    out_path = Path(response["translated_files"][0])
    assert out_path.exists()
    body = out_path.read_text(encoding="utf-8")
    # Edited translation is in the regenerated file.
    assert "高山" in body
    assert "你好" in body


def test_regenerate_outputs_uses_manual_edit_from_failed_subtask(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "sample.txt").write_text("안녕\n", encoding="utf-8")
    _seed_translation_task(
        service,
        task_id="translation-pf-failed-edit-regen",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("0:0", "안녕", "안녕")],
        status=TaskStatus.FAILED,
    )
    subtask = service.cache.load_subtasks("translation-pf-failed-edit-regen")[0]
    service.cache.save_subtask(replace(subtask, status=SubtaskStatus.FAILED))

    before = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-failed-edit-regen"},
    )
    assert before["items"][0]["dst"] == "안녕"

    router.call(
        "proofreading.update_segment",
        {
            "task_id": "translation-pf-failed-edit-regen",
            "segment_id": "0:0",
            "dst": "你好",
        },
    )
    after = router.call(
        "proofreading.load_snapshot",
        {"task_id": "translation-pf-failed-edit-regen"},
    )
    assert after["items"][0]["dst"] == "你好"

    response = router.call(
        "proofreading.regenerate_outputs",
        {"task_id": "translation-pf-failed-edit-regen"},
    )
    assert len(response["translated_files"]) == 1
    body = Path(response["translated_files"][0]).read_text(encoding="utf-8")
    assert "你好" in body
    assert "안녕" not in body


def test_regenerate_outputs_does_not_write_source_when_no_segments_match(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "sample.txt").write_text("안녕\n산\n", encoding="utf-8")
    _seed_translation_task(
        service,
        task_id="translation-pf-regen-mismatch",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("9:0", "안녕", "你好"), ("9:1", "산", "山")],
    )

    response = router.call(
        "proofreading.regenerate_outputs",
        {"task_id": "translation-pf-regen-mismatch"},
    )

    assert response["translated_files"] == []
    assert response["bilingual_files"] == []
    assert len(response["failed_files"]) == 1
    assert response["failed_files"][0]["reason"] == (
        "no translated segments matched this file"
    )
    assert response["failed_files"][0]["code"] == "no_matching_translations"
    assert response["failed_files"][0]["details"] == {"expected_segments": 2}
    assert list(output_dir.iterdir()) == []


def test_regenerate_outputs_blocks_completed_cache_segment_mismatch(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "sample.txt").write_text("안녕\n산\n바다\n", encoding="utf-8")
    _seed_translation_task(
        service,
        task_id="translation-pf-completed-mismatch",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("0:0", "안녕", "你好"), ("0:1", "산", "山")],
        status=TaskStatus.COMPLETED,
    )

    response = router.call(
        "proofreading.regenerate_outputs",
        {"task_id": "translation-pf-completed-mismatch"},
    )

    assert response["translated_files"] == []
    assert response["bilingual_files"] == []
    assert len(response["failed_files"]) == 1
    assert response["failed_files"][0]["code"] == "cache_segment_mismatch"
    details = response["failed_files"][0]["details"]
    assert details["expected_segments"] == 3
    assert details["matched_segments"] == 2
    assert details["missing_segments"] == 1
    assert details["first_missing_segment_id"] == "0:2"
    assert len(details["parsed_source_fingerprint"]) == 16
    assert len(details["cache_source_fingerprint"]) == 16
    assert details["parsed_source_fingerprint"] != details["cache_source_fingerprint"]
    assert list(output_dir.iterdir()) == []


def test_regenerate_outputs_reuses_cached_pre_replacements_for_segment_filter(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "sample.txt").write_text(
        "목차\n1권\n악연\n",
        encoding="utf-8",
    )
    _seed_translation_task(
        service,
        task_id="translation-pf-cached-pre-rules",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("0:0", "목차", "目录"), ("0:2", "악연", "孽缘")],
        status=TaskStatus.COMPLETED,
        metadata_overrides={
            "pre_replacements": [
                {
                    "src": r"(\d+)권",
                    "dst": r"第\1卷",
                    "regex": True,
                    "case_sensitive": False,
                    "note": "",
                    "enabled": True,
                }
            ],
        },
    )

    response = router.call(
        "proofreading.regenerate_outputs",
        {"task_id": "translation-pf-cached-pre-rules"},
    )

    assert response["failed_files"] == []
    assert len(response["translated_files"]) == 1
    body = Path(response["translated_files"][0]).read_text(encoding="utf-8")
    assert "目录" in body
    assert "1권" in body
    assert "孽缘" in body


def test_regenerate_outputs_can_export_bilingual_txt(router_and_service):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "sample.txt").write_text("안녕\n산\n", encoding="utf-8")
    _seed_translation_task(
        service,
        task_id="translation-pf-bilingual",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("0:0", "안녕", "你好"), ("0:1", "산", "山")],
    )

    response = router.call(
        "proofreading.regenerate_outputs",
        {"task_id": "translation-pf-bilingual", "bilingual": True},
    )

    assert len(response["translated_files"]) == 1
    assert len(response["bilingual_files"]) == 1
    bilingual_path = Path(response["bilingual_files"][0])
    assert bilingual_path.exists()
    body = bilingual_path.read_text(encoding="utf-8")
    assert "안녕" in body
    assert "你好" in body


def test_regenerate_outputs_rejects_missing_input_dir(router_and_service):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "ghost-input"  # never created
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    _seed_translation_task(
        service,
        task_id="translation-pf-noinput",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("0:0", "안녕", "你好")],
    )
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.regenerate_outputs",
            {"task_id": "translation-pf-noinput"},
        )
    # FileNotFoundError → bridge.not_found
    assert caught.value.code == "bridge.not_found"


def test_regenerate_outputs_rejects_input_dir_with_no_supported_files(
    router_and_service,
):
    router, service, tmp_path = router_and_service
    input_dir = tmp_path / "in-empty"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "readme.md").write_text("not a novel", encoding="utf-8")
    _seed_translation_task(
        service,
        task_id="translation-pf-empty-input",
        input_dir=input_dir,
        output_dir=output_dir,
        file_segments=[("0:0", "안녕", "你好")],
    )
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.regenerate_outputs",
            {"task_id": "translation-pf-empty-input"},
        )
    assert caught.value.code == "bridge.invalid_argument"
    assert "no .epub or .txt" in caught.value.payload.message


def test_regenerate_outputs_rejects_missing_metadata(router_and_service):
    router, service, _tmp = router_and_service
    record = TaskRecord(
        id="translation-pf-nometa",
        kind=TaskKind.TRANSLATION,
        status=TaskStatus.COMPLETED,
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:01:00+00:00",
        metadata={},  # no input_dir/output_dir
    )
    service.cache.write_seed(record, ())
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.regenerate_outputs",
            {"task_id": "translation-pf-nometa"},
        )
    assert caught.value.code == "bridge.invalid_argument"
