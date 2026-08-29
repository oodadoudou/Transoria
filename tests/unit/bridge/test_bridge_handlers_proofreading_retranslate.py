"""Tests for proofreading.retranslate_segment / retranslate_status."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Sequence

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.proofreading import register
from transoria.bridge.task_registry import RunningTask, TaskRegistry
from transoria.bridge.task_service import (
    RetranslateJob,
    TaskService,
    _read_segment_dst,
)
from transoria.domain import (
    Language,
    SubtaskStatus,
    TaskKind,
    TaskStatus,
)
from transoria.llm.client import LlmClient, TransportResult
from transoria.llm.config import ModelConfig, ProviderFormat, ThinkingLevel
from transoria.model_profiles import ModelProfileStore
from transoria.prompts import PromptKind, PromptPreset, PromptPresetStore
from transoria.runtime.cache import TaskCache
from transoria.runtime.subtask import Subtask
from transoria.runtime.task_record import TaskRecord
from transoria.settings import SettingsStore


@dataclass
class _StubTransport:
    prefix: str = "重翻译文"
    block_event: threading.Event | None = None
    fail: bool = False
    scripted_translations: list[str] = field(default_factory=list)
    translations_by_key: dict[str, str] = field(default_factory=dict)
    judge_decisions: list[str] = field(default_factory=list)
    judge_invalid_response: bool = False
    judge_fail: bool = False
    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.requests.append(dict(payload))
        call_index = len(self.requests) - 1
        if self.block_event is not None:
            self.block_event.wait(timeout=5.0)
        if self.fail:
            return TransportResult(500, {"error": "boom"})
        messages = payload["messages"]
        system_message = messages[0]["content"]
        if "translation quality comparator" in system_message:
            if self.judge_fail:
                return TransportResult(500, {"error": "judge boom"})
            if self.judge_invalid_response:
                content = "not-json"
            else:
                comparison = json.loads(messages[-1]["content"])
                if "items" in comparison:
                    decisions = {}
                    for item in comparison["items"]:
                        if self.judge_decisions:
                            decision = self.judge_decisions.pop(0)
                        elif (
                            item["new_candidate"].strip()
                            == item["source"].strip()
                            and item["existing_translation"].strip()
                            != item["source"].strip()
                        ):
                            decision = "keep_existing"
                        else:
                            decision = "accept_new"
                        decisions[item["id"]] = {
                            "decision": decision,
                            "reason": "stub quality decision",
                        }
                    content = json.dumps({"decisions": decisions})
                else:
                    if self.judge_decisions:
                        decision = self.judge_decisions.pop(0)
                    elif (
                        comparison["new_candidate"].strip()
                        == comparison["source"].strip()
                        and comparison["existing_translation"].strip()
                        != comparison["source"].strip()
                    ):
                        decision = "keep_existing"
                    else:
                        decision = "accept_new"
                    content = json.dumps(
                        {"decision": decision, "reason": "stub quality decision"}
                    )
            return TransportResult(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": content}}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7},
                },
            )
        user_message = payload["messages"][-1]["content"]
        translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
        lines: list[str] = []
        for line in translate_section.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            for key, value in parsed.items():
                translated = (
                    self.translations_by_key[key]
                    if key in self.translations_by_key
                    else self.scripted_translations[call_index]
                    if call_index < len(self.scripted_translations)
                    else f"{self.prefix}{key}"
                )
                lines.append(
                    json.dumps({key: translated}, ensure_ascii=False)
                )
        return TransportResult(
            200,
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "\n".join(lines)}}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
            },
        )


@dataclass
class _ConcurrencyTransport(_StubTransport):
    active: int = 0
    max_active: int = 0
    active_lock: threading.Lock = field(default_factory=threading.Lock)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        with self.active_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            return await super().execute(url, headers, payload, timeout)
        finally:
            with self.active_lock:
                self.active -= 1


def _make_service(
    tmp_path: Path,
    *,
    transport: _StubTransport | None = None,
    concurrency_limit: int = 0,
    input_token_limit: int = 0,
) -> TaskService:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    profile_store = ModelProfileStore.from_cache_root(cache_root)
    profile = ModelConfig(
        id="test-profile",
        display_name="Test Model",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="m",
        api_keys=("test-key",),
        thinking_level=ThinkingLevel.OFF,
        concurrency_limit=concurrency_limit,
        input_token_limit=input_token_limit,
        rpm_limit=0,
    )
    profile_store.create(profile)
    profile_store.set_api_keys(profile.id, ("test-key",))
    settings_store = SettingsStore(path=cache_root / "settings.json")
    settings_store.save_partial("app", {"active_translation_model_id": profile.id})
    return TaskService(
        cache=TaskCache(root=cache_root / "tasks"),
        registry=TaskRegistry(),
        settings_store=settings_store,
        profile_store=profile_store,
        prompts_cache_root=cache_root,
        llm_client_factory=lambda: LlmClient(transport=transport or _StubTransport()),
    )


def _seed_task_with_snapshot(
    service: TaskService,
    *,
    task_id: str = "translation-pf-rt-1",
    segments: Sequence[tuple[str, str, str]] = (("0:0", "안녕", "你好"),),
    glossary: Sequence[Mapping[str, object]] = (),
    post_replacements: Sequence[Mapping[str, object]] = (),
    status: TaskStatus = TaskStatus.COMPLETED,
) -> None:
    record = TaskRecord(
        id=task_id,
        kind=TaskKind.TRANSLATION,
        status=status,
        created_at="2026-05-04T00:00:00+00:00",
        updated_at="2026-05-04T00:01:00+00:00",
        metadata={
            "input_dir": "/in",
            "output_dir": "/out",
            "source_language": Language.KOREAN.value,
            "target_language": Language.CHINESE_SIMPLIFIED.value,
            "model_id": "test-profile",
            "prompt_preset_id": "default-translation-en",
            "prompt_preset": {
                "id": "default-translation-en",
                "name": "Test",
                "kind": "translation",
                "system_prompt": "Translate the text.",
                "suffix_prompt": "",
                "thinking_prompt": "",
                "description": "",
                "enabled": True,
                "is_system": False,
            },
            "glossary": list(glossary),
            "text_preserve_rules": [],
            "pre_replacements": [],
            "post_replacements": list(post_replacements),
        },
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
            for idx, (seg_id, src, _dst) in enumerate(segments)
        ],
        "context_lines": [],
        "glossary_entries": [],
    }
    response_payload = {
        "version": 2,
        "translations": {seg_id: dst for seg_id, _src, dst in segments},
        "low_confidence": [],
    }
    subtask = Subtask(
        id="chunk-00000",
        task_id=task_id,
        status=SubtaskStatus.COMPLETED,
        request_payload=request_payload,
        response_content=json.dumps(response_payload, ensure_ascii=False),
    )
    service.cache.write_seed(record, (subtask,))


@pytest.fixture
def router_and_service(tmp_path: Path):
    transport = _StubTransport()
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    return router, service, transport


def _wait_for_status(service: TaskService, request_id: str, expected: set[str]):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        result = service.read_retranslate_status(request_id=request_id)
        if result["status"] in expected:
            return result
        time.sleep(0.02)
    pytest.fail(
        f"timed out waiting for {expected}; last status was "
        f"{service.read_retranslate_status(request_id=request_id)['status']}"
    )


def test_read_segment_dst_matches_proofreading_latest_subtask_wins(
    router_and_service,
):
    _router, service, _t = router_and_service
    _seed_task_with_snapshot(service)
    snapshot = service.cache.load("translation-pf-rt-1")
    first = snapshot.subtasks[0]
    stale_payload = json.loads(first.response_content)
    stale_payload["translations"]["0:0"] = "旧译文"
    latest_payload = json.loads(first.response_content)
    latest_payload["translations"]["0:0"] = "最新译文"
    service.cache.save_subtask(
        replace(first, response_content=json.dumps(stale_payload, ensure_ascii=False))
    )
    service.cache.save_subtask(
        replace(
            first,
            id="chunk-00001",
            response_content=json.dumps(latest_payload, ensure_ascii=False),
        )
    )

    snapshot = service.cache.load("translation-pf-rt-1")

    assert _read_segment_dst(snapshot, "0:0") == "最新译文"
    with service._retranslate_task_lock("translation-pf-rt-1"):
        indexed_snapshot = service._load_retranslate_snapshot(
            "translation-pf-rt-1", ("0:0",)
        )
    assert _read_segment_dst(indexed_snapshot, "0:0") == "最新译文"


def test_retranslate_happy_path_writes_new_dst_to_cache(router_and_service):
    router, service, _t = router_and_service
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    request_id = response["request_id"]
    final = _wait_for_status(service, request_id, {"completed", "failed", "stale"})
    assert final["status"] == "completed", final
    assert final["result_dst"] == "重翻译文0"
    assert final["model_id"] == "test-profile"
    assert final["segment_count"] == 1
    assert final["created_at"]
    assert final["elapsed_seconds"] >= 0

    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "重翻译文0"
    persisted = service._load_retranslate_job(request_id)  # type: ignore[attr-defined]
    assert persisted is not None
    assert persisted.cache_applied is True


def test_retranslate_preserved_role_markers_replace_truncated_existing(
    tmp_path: Path,
):
    source = (
        "A는 B로 위장해 서커스단에 잠입했고, 동생을 살해하는 데 성공했다. "
        "그러나 현장에서 도주하던 중 보조 곡예사 C에게 오인당해 B로서 "
        "무대에 오르게 되었다. 동생과 달리 곡예를 배우지 못한 A는 그대로 "
        "추락해…」"
    )
    candidate = (
        "A伪装成B潜入马戏团，成功杀害了弟弟。然而在逃离现场途中，被助理"
        "杂技演员C误认，以B的身份登上了舞台。与弟弟不同，从未学过杂技的A"
        "就这样坠落……」"
    )
    transport = _StubTransport(translations_by_key={"0": candidate})
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(
        service,
        segments=(("0:1089", source, "男人就那样坠落了下去。"),),
    )

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:1089"},
    )
    final = _wait_for_status(
        service,
        response["request_id"],
        {"completed", "failed", "unresolved"},
    )

    assert final["status"] == "completed", final
    assert final["result_dst"] == candidate
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:1089"] == candidate


def test_retranslate_writes_live_and_completed_request_log_events(tmp_path: Path):
    release = threading.Event()
    transport = _StubTransport(block_event=release)
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    deadline = time.monotonic() + 5.0
    while not transport.requests and time.monotonic() < deadline:
        time.sleep(0.01)

    live = service.read_request_events(
        kind="translation",
        task_id="translation-pf-rt-1",
    )
    assert live["events"][0]["status"] == "running"
    assert live["events"][0]["model_profile_id"] == "test-profile"
    assert live["events"][0]["subtask_id"].startswith(
        "proofreading-retranslate-"
    )

    release.set()
    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed"}
    )
    completed = service.read_request_events(
        kind="translation",
        task_id="translation-pf-rt-1",
    )

    assert final["status"] == "completed"
    assert completed["events"][0]["status"] == "completed"
    assert completed["events"][0]["phase"] == "completed"


def test_retranslate_status_does_not_wait_for_task_cache_updates(
    router_and_service,
):
    _router, service, _transport = router_and_service
    job = RetranslateJob(
        request_id="retranslate-status-readable",
        task_id="translation-pf-rt-1",
        segment_id="0:0",
        original_dst="旧译文",
        status="running",
        created_at_wall="2026-05-04T00:00:00+00:00",
        updated_at_wall="2026-05-04T00:00:01+00:00",
    )
    with service._retranslate_lock:
        service._retranslate_jobs[job.request_id] = job

    task_lock = service._retranslate_task_lock(job.task_id)
    task_lock.acquire()
    finished = threading.Event()
    result: dict[str, object] = {}

    def read_status() -> None:
        result.update(service.read_retranslate_status(request_id=job.request_id))
        finished.set()

    reader = threading.Thread(target=read_status)
    reader.start()
    try:
        assert finished.wait(timeout=0.5)
    finally:
        task_lock.release()
        reader.join(timeout=1.0)

    assert result["status"] == "running"


def test_retranslate_statuses_returns_multiple_jobs(router_and_service):
    router, service, _transport = router_and_service
    jobs = [
        RetranslateJob(
            request_id=f"retranslate-status-{index}",
            task_id="translation-pf-rt-1",
            segment_id=f"0:{index}",
            original_dst="旧译文",
            status="running",
            created_at_wall="2026-05-04T00:00:00+00:00",
            updated_at_wall="2026-05-04T00:00:01+00:00",
        )
        for index in range(2)
    ]
    with service._retranslate_lock:
        for job in jobs:
            service._retranslate_jobs[job.request_id] = job

    result = router.call(
        "proofreading.retranslate_statuses",
        {"request_ids": [job.request_id for job in jobs]},
    )

    assert [status["request_id"] for status in result["statuses"]] == [
        job.request_id for job in jobs
    ]
    assert all(status["status"] == "running" for status in result["statuses"])


def test_retranslate_snapshot_index_avoids_reloading_all_subtasks(
    router_and_service, monkeypatch: pytest.MonkeyPatch
):
    _router, service, _transport = router_and_service
    _seed_task_with_snapshot(
        service,
        segments=(("0:0", "첫째", "第一"), ("0:1", "둘째", "第二")),
    )
    original_load_subtasks = TaskCache.load_subtasks
    full_loads = 0

    def tracked_load_subtasks(cache: TaskCache, task_id: str):
        nonlocal full_loads
        full_loads += 1
        return original_load_subtasks(cache, task_id)

    monkeypatch.setattr(TaskCache, "load_subtasks", tracked_load_subtasks)

    with service._retranslate_task_lock("translation-pf-rt-1"):
        first = service._load_retranslate_snapshot(
            "translation-pf-rt-1", ("0:0",)
        )
        second = service._load_retranslate_snapshot(
            "translation-pf-rt-1", ("0:1",)
        )

    assert _read_segment_dst(first, "0:0") == "第一"
    assert _read_segment_dst(second, "0:1") == "第二"
    assert full_loads == 1


def test_retranslate_batch_sends_five_segments_in_one_request_and_patches_all(
    router_and_service,
):
    router, service, transport = router_and_service
    segments = tuple(
        (f"0:{index}", f"원문 {index}", f"旧译文 {index}")
        for index in range(5)
    )
    _seed_task_with_snapshot(service, segments=segments)

    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "segment_ids": [segment_id for segment_id, _src, _dst in segments],
        },
    )
    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed"}
    )

    assert final["status"] == "completed", final
    assert len(transport.requests) == 1
    assert transport.requests[0]["stream"] is True
    assert [item["status"] for item in final["results"]] == ["completed"] * 5
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"] == {
        f"0:{index}": f"重翻译文{index}" for index in range(5)
    }


def test_retranslate_batch_keeps_successes_and_preserves_unresolved_segment(
    tmp_path: Path,
):
    transport = _StubTransport(
        translations_by_key={
            "0": "译文零",
            "1": "译文一",
            "2": "译文二",
            "3": "译文三",
            "4": "원문 4",
        }
    )
    service = _make_service(tmp_path, transport=transport)
    service.settings_store.save_partial(
        "translation",
        {"low_confidence_max_retries": 0, "request_retry_attempts": 0},
    )
    router = BridgeRouter()
    register(router, service=service)
    segments = tuple(
        (f"0:{index}", f"원문 {index}", f"旧译文 {index}")
        for index in range(5)
    )
    _seed_task_with_snapshot(service, segments=segments)

    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "segment_ids": [item[0] for item in segments],
        },
    )
    final = _wait_for_status(service, response["request_id"], {"completed", "failed"})

    statuses = {item["segment_id"]: item["status"] for item in final["results"]}
    assert statuses == {
        "0:0": "completed",
        "0:1": "completed",
        "0:2": "completed",
        "0:3": "completed",
        "0:4": "unresolved",
    }
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "译文零"
    assert payload["translations"]["0:4"] == "旧译文 4"


def test_retranslate_batch_applies_exact_non_prose_content(tmp_path: Path):
    title = "∥8. Moderato piano p."
    transport = _StubTransport(
        translations_by_key={"0": title, "1": "<ㅇ"}
    )
    service = _make_service(tmp_path, transport=transport)
    service.settings_store.save_partial(
        "translation",
        {"low_confidence_max_retries": 0, "request_retry_attempts": 0},
    )
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(
        service,
        segments=(
            ("0:0", title, "与标题无关的旧译文。"),
            ("0:1", "<ㅇ", "与表情无关的旧译文。"),
        ),
    )

    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "segment_ids": ["0:0", "0:1"],
        },
    )
    final = _wait_for_status(service, response["request_id"], {"completed", "failed"})

    assert final["status"] == "completed", final
    assert [item["status"] for item in final["results"]] == ["completed"] * 2
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == title
    assert payload["translations"]["0:1"] == "<ㅇ"


def test_retranslate_single_source_echo_is_unresolved_and_not_written(
    tmp_path: Path,
):
    transport = _StubTransport(translations_by_key={"0": "안녕"})
    service = _make_service(tmp_path, transport=transport)
    service.settings_store.save_partial(
        "translation",
        {"low_confidence_max_retries": 0, "request_retry_attempts": 0},
    )
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(
        service, segments=(("0:0", "안녕", "已有的较好译文"),)
    )

    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "segment_ids": ["0:0"],
        },
    )
    final = _wait_for_status(service, response["request_id"], {"unresolved", "failed"})

    assert final["status"] == "unresolved"
    assert final["last_translation"] == "안녕"
    assert final["results"] == [
        {
            "segment_id": "0:0",
            "status": "unresolved",
            "error": (
                "quality review kept the existing translation: "
                "stub quality decision"
            ),
        }
    ]
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "已有的较好译文"


def test_single_retranslate_quality_review_accepts_exact_symbol_preservation(
    tmp_path: Path,
):
    transport = _StubTransport(
        translations_by_key={"0": "ㅋㅋㅋㅋ!!"},
        judge_decisions=["accept_new"],
    )
    service = _make_service(tmp_path, transport=transport)
    service.settings_store.save_partial(
        "translation",
        {"low_confidence_max_retries": 0, "request_retry_attempts": 0},
    )
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(
        service,
        segments=(("0:0", "ㅋㅋㅋㅋ!!", "一个与原文无关的句子。"),),
    )

    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "segment_ids": ["0:0"],
        },
    )
    final = _wait_for_status(service, response["request_id"], {"completed", "failed"})

    assert final["status"] == "completed", final
    assert final["result_dst"] == "ㅋㅋㅋㅋ!!"
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "ㅋㅋㅋㅋ!!"


def test_single_retranslate_quality_review_rejects_unrelated_target_prose(
    tmp_path: Path,
):
    transport = _StubTransport(
        translations_by_key={"0": "男人就那样坠落了下去。"},
        judge_decisions=["keep_existing"],
    )
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(
        service,
        segments=(("0:0", "ㅋㅋㅋㅋ!!", "ㅋㅋㅋㅋ!!"),),
    )

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    final = _wait_for_status(service, response["request_id"], {"unresolved", "failed"})

    assert final["status"] == "unresolved", final
    assert "quality review kept" in final["error"]
    assert final["last_translation"] == "男人就那样坠落了下去。"
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "ㅋㅋㅋㅋ!!"


@pytest.mark.parametrize("judge_failure", ["request", "invalid_response"])
def test_single_retranslate_quality_review_failure_keeps_existing(
    tmp_path: Path,
    judge_failure: str,
):
    transport = _StubTransport(
        translations_by_key={"0": "新的候选译文。"},
        judge_fail=judge_failure == "request",
        judge_invalid_response=judge_failure == "invalid_response",
    )
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(
        service,
        segments=(("0:0", "원문", "现有译文。"),),
    )

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    final = _wait_for_status(service, response["request_id"], {"unresolved", "failed"})

    assert final["status"] == "unresolved", final
    assert "could not verify" in final["error"]
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "现有译文。"


def test_single_retranslate_quality_reviews_share_dynamic_batch(tmp_path: Path):
    transport = _StubTransport(
        translations_by_key={"0": "候选译文。"},
        judge_decisions=["accept_new", "keep_existing", "accept_new"],
    )
    service = _make_service(tmp_path, transport=transport, concurrency_limit=3)
    router = BridgeRouter()
    register(router, service=service)
    segments = tuple(
        (f"0:{index}", f"원문 {index}", f"旧译文 {index}") for index in range(3)
    )
    _seed_task_with_snapshot(service, segments=segments)

    request_ids = [
        router.call(
            "proofreading.retranslate_segment",
            {"task_id": "translation-pf-rt-1", "segment_id": segment_id},
        )["request_id"]
        for segment_id, _src, _dst in segments
    ]
    finals = [
        _wait_for_status(
            service,
            request_id,
            {"completed", "unresolved", "failed"},
        )
        for request_id in request_ids
    ]

    assert [item["status"] for item in finals] == [
        "completed",
        "unresolved",
        "completed",
    ]
    quality_requests = [
        request
        for request in transport.requests
        if "translation quality comparator"
        in request["messages"][0]["content"]
    ]
    assert len(quality_requests) == 1
    quality_payload = json.loads(quality_requests[0]["messages"][-1]["content"])
    assert len(quality_payload["items"]) == 3
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"] == {
        "0:0": "候选译文。",
        "0:1": "旧译文 1",
        "0:2": "候选译文。",
    }


def test_single_retranslate_quality_batch_caps_at_five_items(tmp_path: Path):
    transport = _StubTransport(translations_by_key={"0": "候选译文。"})
    service = _make_service(tmp_path, transport=transport, concurrency_limit=6)
    router = BridgeRouter()
    register(router, service=service)
    segments = tuple(
        (f"0:{index}", f"원문 {index}", f"旧译文 {index}") for index in range(6)
    )
    _seed_task_with_snapshot(service, segments=segments)

    request_ids = [
        router.call(
            "proofreading.retranslate_segment",
            {"task_id": "translation-pf-rt-1", "segment_id": segment_id},
        )["request_id"]
        for segment_id, _src, _dst in segments
    ]
    finals = [
        _wait_for_status(service, request_id, {"completed", "failed"})
        for request_id in request_ids
    ]

    assert [item["status"] for item in finals] == ["completed"] * 6
    quality_payloads = [
        json.loads(request["messages"][-1]["content"])
        for request in transport.requests
        if "translation quality comparator"
        in request["messages"][0]["content"]
    ]
    batch_sizes = sorted(
        len(payload.get("items", [payload])) for payload in quality_payloads
    )
    assert batch_sizes == [1, 5]


def test_single_retranslate_quality_batch_honors_model_input_budget(
    tmp_path: Path,
):
    transport = _StubTransport(translations_by_key={"0": "候选译文。"})
    service = _make_service(
        tmp_path,
        transport=transport,
        concurrency_limit=2,
        input_token_limit=1,
    )
    router = BridgeRouter()
    register(router, service=service)
    segments = (
        ("0:0", "较长的韩语原文一", "旧译文一"),
        ("0:1", "较长的韩语原文二", "旧译文二"),
    )
    _seed_task_with_snapshot(service, segments=segments)

    request_ids = [
        router.call(
            "proofreading.retranslate_segment",
            {"task_id": "translation-pf-rt-1", "segment_id": segment_id},
        )["request_id"]
        for segment_id, _src, _dst in segments
    ]
    finals = [
        _wait_for_status(service, request_id, {"completed", "failed"})
        for request_id in request_ids
    ]

    assert [item["status"] for item in finals] == ["completed"] * 2
    quality_payloads = [
        json.loads(request["messages"][-1]["content"])
        for request in transport.requests
        if "translation quality comparator"
        in request["messages"][0]["content"]
    ]
    assert len(quality_payloads) == 2
    assert all("items" not in payload for payload in quality_payloads)


def test_single_retranslate_invalid_quality_batch_keeps_existing(
    tmp_path: Path,
):
    transport = _StubTransport(
        translations_by_key={"0": "候选译文。"},
        judge_invalid_response=True,
    )
    service = _make_service(tmp_path, transport=transport, concurrency_limit=2)
    router = BridgeRouter()
    register(router, service=service)
    segments = (
        ("0:0", "원문 0", "旧译文 0"),
        ("0:1", "원문 1", "旧译文 1"),
    )
    _seed_task_with_snapshot(service, segments=segments)

    request_ids = [
        router.call(
            "proofreading.retranslate_segment",
            {"task_id": "translation-pf-rt-1", "segment_id": segment_id},
        )["request_id"]
        for segment_id, _src, _dst in segments
    ]
    finals = [
        _wait_for_status(service, request_id, {"unresolved", "failed"})
        for request_id in request_ids
    ]

    assert [item["status"] for item in finals] == ["unresolved"] * 2
    quality_requests = [
        request
        for request in transport.requests
        if "translation quality comparator"
        in request["messages"][0]["content"]
    ]
    assert len(quality_requests) == 1
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"] == {
        "0:0": "旧译文 0",
        "0:1": "旧译文 1",
    }


def test_retranslate_batch_keeps_user_edited_segment_stale(tmp_path: Path):
    block = threading.Event()
    transport = _StubTransport(block_event=block)
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    segments = (
        ("0:0", "원문 0", "旧译文 0"),
        ("0:1", "원문 1", "旧译文 1"),
    )
    _seed_task_with_snapshot(service, segments=segments)

    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "segment_ids": ["0:0", "0:1"],
        },
    )
    deadline = time.monotonic() + 5.0
    while not transport.requests and time.monotonic() < deadline:
        time.sleep(0.01)
    router.call(
        "proofreading.update_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:1",
            "dst": "用户手动改的",
        },
    )
    block.set()

    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed"}
    )

    statuses = {item["segment_id"]: item["status"] for item in final["results"]}
    assert statuses == {"0:0": "completed", "0:1": "stale"}
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "重翻译文0"
    assert payload["translations"]["0:1"] == "用户手动改的"


def test_retranslate_batch_status_survives_memory_gc(router_and_service):
    router, service, _transport = router_and_service
    segments = (
        ("0:0", "원문 0", "旧译文 0"),
        ("0:1", "원문 1", "旧译文 1"),
    )
    _seed_task_with_snapshot(service, segments=segments)
    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "segment_ids": ["0:0", "0:1"],
        },
    )
    request_id = response["request_id"]
    final = _wait_for_status(service, request_id, {"completed", "failed"})
    assert final["status"] == "completed"

    service._retranslate_jobs.pop(request_id, None)  # type: ignore[attr-defined]
    persisted = service.read_retranslate_status(request_id=request_id)

    assert persisted["status"] == "completed"
    assert [item["segment_id"] for item in persisted["results"]] == ["0:0", "0:1"]


def test_retranslate_batch_rejects_more_than_five_segments(router_and_service):
    router, service, _transport = router_and_service
    segments = tuple(
        (f"0:{index}", f"원문 {index}", f"旧译文 {index}")
        for index in range(6)
    )
    _seed_task_with_snapshot(service, segments=segments)

    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.retranslate_segment",
            {
                "task_id": "translation-pf-rt-1",
                "segment_id": "0:0",
                "segment_ids": [item[0] for item in segments],
            },
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_retranslate_uses_low_confidence_retry_to_prefer_target_language(
    tmp_path: Path,
):
    transport = _StubTransport(scripted_translations=["안녕", "你好"])
    service = _make_service(tmp_path, transport=transport)
    service.settings_store.save_partial(
        "translation",
        {"low_confidence_max_retries": 1, "request_retry_attempts": 0},
    )
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service, segments=(("0:0", "안녕", "안녕"),))

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed", "stale"}
    )

    assert final["status"] == "completed", final
    assert final["result_dst"] == "你好"
    assert len(transport.requests) == 3
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "你好"


def test_retranslate_uses_current_active_translation_model(router_and_service):
    router, service, transport = router_and_service
    current_profile = ModelConfig(
        id="current-profile",
        display_name="Current Model",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example-current/api/v1/",
        model_id="current-model",
        api_keys=("current-key",),
        thinking_level=ThinkingLevel.OFF,
        rpm_limit=0,
    )
    service.profile_store.create(current_profile)
    service.profile_store.set_api_keys(current_profile.id, ("current-key",))
    service.settings_store.save_partial(
        "app", {"active_translation_model_id": current_profile.id}
    )
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed", "stale"}
    )

    assert final["status"] == "completed", final
    assert transport.requests
    assert transport.requests[0]["model"] == "current-model"


def test_retranslate_uses_current_active_translation_prompt(router_and_service):
    router, service, transport = router_and_service
    PromptPresetStore(
        path=service.prompts_cache_root / "prompts.translation.json",
        kind=PromptKind.TRANSLATION,
    ).save(
        [
            PromptPreset(
                id="current-translation-prompt",
                name="Current Translation Prompt",
                kind=PromptKind.TRANSLATION,
                system_prompt="Current prompt from translation settings.",
            )
        ]
    )
    service.settings_store.save_partial(
        "app", {"active_translation_prompt_id": "current-translation-prompt"}
    )
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed", "stale"}
    )

    assert final["status"] == "completed", final
    messages = transport.requests[0]["messages"]
    assert any(
        isinstance(message, Mapping)
        and "Current prompt from translation settings." in str(message.get("content", ""))
        for message in messages
    )


def test_retranslate_accepts_local_model_and_prompt_overrides(router_and_service):
    router, service, transport = router_and_service
    override_profile = ModelConfig(
        id="override-profile",
        display_name="Override Model",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example-override/api/v1/",
        model_id="override-model",
        api_keys=("override-key",),
        thinking_level=ThinkingLevel.OFF,
        rpm_limit=0,
    )
    service.profile_store.create(override_profile)
    service.profile_store.set_api_keys(override_profile.id, ("override-key",))
    PromptPresetStore(
        path=service.prompts_cache_root / "prompts.translation.json",
        kind=PromptKind.TRANSLATION,
    ).save(
        [
            PromptPreset(
                id="proofreading-prompt",
                name="Proofreading Prompt",
                kind=PromptKind.TRANSLATION,
                system_prompt="Proofread translate.",
                suffix_prompt="Use this override.",
            )
        ]
    )
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "model_id": "override-profile",
            "prompt_preset_id": "proofreading-prompt",
        },
    )
    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed", "stale"}
    )

    assert final["status"] == "completed", final
    assert transport.requests[0]["model"] == "override-model"
    messages = transport.requests[0]["messages"]
    assert any(
        isinstance(message, Mapping)
        and "Proofread translate." in str(message.get("content", ""))
        for message in messages
    )


def test_retranslate_rejects_missing_metadata(router_and_service):
    router, service, _t = router_and_service
    record = TaskRecord(
        id="translation-pf-no-meta",
        kind=TaskKind.TRANSLATION,
        status=TaskStatus.COMPLETED,
        created_at="2026-05-04T00:00:00+00:00",
        updated_at="2026-05-04T00:01:00+00:00",
        metadata={"model_id": "test-profile"},
    )
    request_payload = {
        "segments": [
            {
                "segment_id": "0:0",
                "chunk_index": 0,
                "prompt_text": "x",
                "original_text": "x",
                "protection_spans": [],
                "leading_whitespace": "",
                "trailing_whitespace": "",
            }
        ]
    }
    service.cache.write_seed(
        record,
        (Subtask(id="c", task_id=record.id, request_payload=request_payload),),
    )

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": record.id, "segment_id": "0:0"},
    )
    final = _wait_for_status(
        service, response["request_id"], {"failed", "completed", "stale"}
    )
    assert final["status"] == "failed"
    assert "prompt_preset" in final["error"] or "predates" in final["error"]


def test_retranslate_rejects_unknown_segment(router_and_service):
    router, service, _t = router_and_service
    _seed_task_with_snapshot(service)
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.retranslate_segment",
            {"task_id": "translation-pf-rt-1", "segment_id": "99:99"},
        )
    assert caught.value.code == "bridge.not_found"


def test_retranslate_allows_stopped_translation_task(router_and_service):
    router, service, _t = router_and_service
    _seed_task_with_snapshot(service, status=TaskStatus.STOPPED)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )

    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed", "stale"}
    )
    assert final["status"] == "completed", final


def test_retranslate_rejects_running_translation_task(router_and_service):
    router, service, _t = router_and_service
    _seed_task_with_snapshot(service, status=TaskStatus.RUNNING)
    service.registry.add(
        RunningTask(
            task_id="translation-pf-rt-1",
            kind="translation",
            cache=service.cache,
            created_at="2026-05-04T00:00:00+00:00",
        )
    )

    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.retranslate_segment",
            {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
        )

    assert caught.value.code == "bridge.conflict"
    assert caught.value.payload.details["status"] == "running"


def test_retranslate_rejects_concurrent_job_for_same_segment(tmp_path: Path):
    block = threading.Event()
    transport = _StubTransport(block_event=block)
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service)

    first = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    try:
        with pytest.raises(BridgeError) as caught:
            router.call(
                "proofreading.retranslate_segment",
                {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
            )
        assert caught.value.code == "bridge.conflict"
    finally:
        block.set()
        _wait_for_status(
            service, first["request_id"], {"completed", "failed", "stale"}
        )


def test_retranslate_enforces_model_concurrency_across_single_requests(
    tmp_path: Path,
):
    release = threading.Event()
    transport = _ConcurrencyTransport(block_event=release)
    service = _make_service(
        tmp_path,
        transport=transport,
        concurrency_limit=2,
    )
    router = BridgeRouter()
    register(router, service=service)
    segments = tuple(
        (f"0:{index}", f"원문 {index}", f"旧译文 {index}")
        for index in range(4)
    )
    _seed_task_with_snapshot(service, segments=segments)

    requests = [
        router.call(
            "proofreading.retranslate_segment",
            {
                "task_id": "translation-pf-rt-1",
                "segment_id": segment_id,
            },
        )["request_id"]
        for segment_id, _src, _dst in segments
    ]
    deadline = time.monotonic() + 5.0
    while len(transport.requests) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    statuses = [
        service.read_retranslate_status(request_id=request_id)["status"]
        for request_id in requests
    ]
    assert len(transport.requests) == 2
    assert transport.max_active == 2
    assert statuses.count("running") == 2
    assert statuses.count("pending") == 2

    release.set()
    finals = [
        _wait_for_status(service, request_id, {"completed", "failed"})
        for request_id in requests
    ]

    assert [item["status"] for item in finals] == ["completed"] * 4
    assert transport.max_active == 2
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert all(
        payload["translations"][segment_id] == "重翻译文0"
        for segment_id, _src, _dst in segments
    )


def test_retranslate_marks_stale_when_user_edits_during_flight(tmp_path: Path):
    block = threading.Event()
    transport = _StubTransport(block_event=block)
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    router.call(
        "proofreading.update_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "dst": "用户手动改的",
        },
    )
    block.set()

    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed", "stale"}
    )
    assert final["status"] == "stale"

    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "用户手动改的"


def test_retranslate_marks_failed_on_llm_error(tmp_path: Path):
    transport = _StubTransport(fail=True)
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    final = _wait_for_status(
        service, response["request_id"], {"completed", "failed", "stale"}
    )
    assert final["status"] == "failed"
    assert final["error"]
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "你好"


def test_retranslate_status_survives_memory_gc_from_disk(router_and_service):
    router, service, _t = router_and_service
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    request_id = response["request_id"]
    final = _wait_for_status(service, request_id, {"completed", "failed", "stale"})
    assert final["status"] == "completed", final

    service._retranslate_jobs.pop(request_id, None)  # type: ignore[attr-defined]
    persisted = service.read_retranslate_status(request_id=request_id)

    assert persisted["status"] == "completed"
    assert persisted["result_dst"] == "重翻译文0"
    assert persisted["attempts"] == 1
    assert persisted["last_translation"] == "重翻译文0"


def test_completed_retranslate_status_repairs_missing_cache_write(router_and_service):
    _router, service, _t = router_and_service
    _seed_task_with_snapshot(service)
    request_id = "retranslate-repair001"
    service._save_retranslate_job(  # type: ignore[attr-defined]
        RetranslateJob(
            request_id=request_id,
            task_id="translation-pf-rt-1",
            segment_id="0:0",
            original_dst="你好",
            status="completed",
            result_dst="重翻译文0",
            last_translation="重翻译文0",
            created_at=time.monotonic(),
        )
    )

    status = service.read_retranslate_status(request_id=request_id)

    assert status["status"] == "completed"
    assert status["result_dst"] == "重翻译文0"
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "重翻译文0"


def test_completed_retranslate_status_does_not_overwrite_user_edit(
    router_and_service,
):
    router, service, _t = router_and_service
    _seed_task_with_snapshot(service)
    router.call(
        "proofreading.update_segment",
        {
            "task_id": "translation-pf-rt-1",
            "segment_id": "0:0",
            "dst": "用户手动改的",
        },
    )
    request_id = "retranslate-stale001"
    service._save_retranslate_job(  # type: ignore[attr-defined]
        RetranslateJob(
            request_id=request_id,
            task_id="translation-pf-rt-1",
            segment_id="0:0",
            original_dst="你好",
            status="completed",
            result_dst="重翻译文0",
            last_translation="重翻译文0",
            created_at=time.monotonic(),
        )
    )

    status = service.read_retranslate_status(request_id=request_id)

    assert status["status"] == "completed"
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "用户手动改的"


def test_resume_completed_retranslate_does_not_rerun(router_and_service):
    router, service, transport = router_and_service
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    request_id = response["request_id"]
    final = _wait_for_status(service, request_id, {"completed", "failed", "stale"})
    assert final["status"] == "completed", final
    before = len(transport.requests)

    service._retranslate_jobs.pop(request_id, None)  # type: ignore[attr-defined]
    resumed = router.call(
        "proofreading.resume_retranslate",
        {"request_id": request_id},
    )
    time.sleep(0.05)

    assert resumed["status"] == "completed"
    assert len(transport.requests) == before


def test_resume_failed_retranslate_from_disk_retries_unfinished(tmp_path: Path):
    transport = _StubTransport(fail=True)
    service = _make_service(tmp_path, transport=transport)
    service.settings_store.save_partial("translation", {"request_retry_attempts": 0})
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    request_id = response["request_id"]
    failed = _wait_for_status(service, request_id, {"failed"})
    assert failed["attempts"] == 1

    service._retranslate_jobs.pop(request_id, None)  # type: ignore[attr-defined]
    transport.fail = False
    resumed = router.call(
        "proofreading.resume_retranslate",
        {"request_id": request_id},
    )
    assert resumed["status"] in {"pending", "running"}

    final = _wait_for_status(service, request_id, {"completed", "failed", "stale"})
    assert final["status"] == "completed", final
    assert final["attempts"] == 2
    assert len(transport.requests) == 3


def test_resume_failed_retranslate_uses_persisted_model_snapshot(tmp_path: Path):
    transport = _StubTransport(fail=True)
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    request_id = response["request_id"]
    failed = _wait_for_status(service, request_id, {"failed"})
    assert failed["attempts"] == 1
    assert transport.requests[-1]["model"] == "m"

    service.profile_store.update("test-profile", {"model_id": "changed-model"})
    service._retranslate_jobs.pop(request_id, None)  # type: ignore[attr-defined]
    transport.fail = False

    resumed = router.call(
        "proofreading.resume_retranslate",
        {"request_id": request_id},
    )
    assert resumed["status"] in {"pending", "running"}

    final = _wait_for_status(service, request_id, {"completed", "failed", "stale"})
    assert final["status"] == "completed", final
    assert final["attempts"] == 2
    assert transport.requests[-1]["model"] == "m"


def test_retranslate_skips_when_source_changes_during_flight(tmp_path: Path):
    block = threading.Event()
    transport = _StubTransport(block_event=block)
    service = _make_service(tmp_path, transport=transport)
    router = BridgeRouter()
    register(router, service=service)
    _seed_task_with_snapshot(service)

    response = router.call(
        "proofreading.retranslate_segment",
        {"task_id": "translation-pf-rt-1", "segment_id": "0:0"},
    )
    request_id = response["request_id"]
    deadline = time.monotonic() + 5.0
    while not transport.requests and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transport.requests
    snapshot = service.cache.load("translation-pf-rt-1")
    subtask = snapshot.subtasks[0]
    payload = dict(subtask.request_payload)
    segments = [dict(segment) for segment in payload["segments"]]
    segments[0]["original_text"] = "바뀜"
    segments[0]["prompt_text"] = "바뀜"
    payload["segments"] = segments
    service.cache.save_subtask(replace(subtask, request_payload=payload))
    block.set()

    final = _wait_for_status(
        service, request_id, {"completed", "failed", "stale", "skipped"}
    )

    assert final["status"] == "skipped", final
    assert "source segment changed" in final["error"]
    assert final["last_translation"] == "重翻译文0"


def test_retranslate_status_returns_not_found_for_unknown_request(
    router_and_service,
):
    router, _service, _t = router_and_service
    with pytest.raises(BridgeError) as caught:
        router.call(
            "proofreading.retranslate_status",
            {"request_id": "retranslate-doesnotexist"},
        )
    assert caught.value.code == "bridge.not_found"


def test_retranslate_job_dict_evicts_oldest_above_50(tmp_path: Path):
    service = _make_service(tmp_path)
    from transoria.bridge.task_service import RetranslateJob

    base = time.monotonic()
    for i in range(60):
        rid = f"retranslate-{i:04d}"
        service._retranslate_jobs[rid] = RetranslateJob(  # type: ignore[attr-defined]
            request_id=rid,
            task_id="t",
            segment_id="0:0",
            original_dst="",
            status="completed",
            created_at=base + i,
        )
    service._gc_retranslate_jobs()  # type: ignore[attr-defined]
    remaining = service._retranslate_jobs  # type: ignore[attr-defined]
    assert len(remaining) <= 50
    assert "retranslate-0000" not in remaining
    assert f"retranslate-{59:04d}" in remaining
