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


def _make_service(
    tmp_path: Path, *, transport: _StubTransport | None = None
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

    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "重翻译文0"


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
            "error": "retranslation did not improve source-language residue.",
        }
    ]
    snapshot = service.cache.load("translation-pf-rt-1")
    payload = json.loads(snapshot.subtasks[0].response_content)
    assert payload["translations"]["0:0"] == "已有的较好译文"


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
    assert len(transport.requests) == 2
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
    assert transport.requests[-1]["model"] == "current-model"


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
    messages = transport.requests[-1]["messages"]
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
    assert transport.requests[-1]["model"] == "override-model"
    messages = transport.requests[-1]["messages"]
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
    assert len(transport.requests) == 2


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
