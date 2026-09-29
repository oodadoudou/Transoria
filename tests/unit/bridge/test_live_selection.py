from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace

import pytest
from openpyxl import Workbook

from tests.helpers.transport import CandidateEmittingTransport, EchoTranslationTransport
from tests.unit.bridge.test_bridge_task_service import (
    _seed_glossary_review_settings, _seed_glossary_settings,
    _seed_translation_settings, _service, _wait_until,
)
from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.model_profiles import register as register_models
from transoria.bridge.handlers.prompts import register as register_prompts
from transoria.bridge.handlers.workflow_presets import register as register_presets
from transoria.bridge.task_registry import RunningTask
from transoria.domain import TaskKind, TaskStatus
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, PromptPresetStore, default_preset
from transoria.runtime import Subtask, TaskRecord


def _router(service):
    router = BridgeRouter()
    register_models(
        router, profile_store=service.profile_store, settings_store=service.settings_store,
        on_selection_changed=service.selection_changed, on_profile_deleted=service.profile_deleted,
    )
    register_prompts(
        router, cache_root=service.prompts_cache_root, settings_store=service.settings_store,
        profile_store=service.profile_store, on_selection_changed=service.selection_changed,
        on_prompt_deleted=service.prompt_deleted,
    )
    register_presets(
        router, cache_root=service.prompts_cache_root, settings_store=service.settings_store,
        profile_store=service.profile_store, on_selection_changed=service.selection_changed,
        on_selection_invalidated=service.selection_invalidated,
    )
    return router


def _seed_inputs(service, tmp_path, kind):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "book.txt").write_text("신해범 이야기", encoding="utf-8")
    if kind == "translation":
        (folder / "book.txt").write_text(
            "\n".join(f"한국어 문장 {i}." for i in range(200)), encoding="utf-8"
        )
        _seed_translation_settings(service, input_dir=folder, output_dir=tmp_path / "out")
    elif kind == "glossary":
        for i in range(5):
            (folder / f"book-{i}.txt").write_text("신해범 이야기", encoding="utf-8")
        _seed_glossary_settings(service, input_dir=folder, output_dir=tmp_path / "out")
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["src", "dst", "info", "frequency"])
        for i in range(6):
            sheet.append([f"이름{i}", f"Name {i}", "人物", 5])
        workbook.save(folder / "terms.xlsx")
        _seed_glossary_review_settings(service, input_dir=folder)
        service.settings_store.save_partial(kind, {"batch_size": 1})
    profile = service.profile_store.load()[0]
    service.profile_store.update(profile.id, {"concurrency_limit": 1, "rpm_limit": 0})
    return service.profile_store.get(profile.id)


class _BlockedTransport:
    def __init__(self, kind):
        self.kind = kind
        self.calls = []
        self.release = threading.Event()
        self.echo = (
            EchoTranslationTransport(prefix="中文", include_source=False)
            if kind == "translation" else CandidateEmittingTransport()
        )

    async def execute(self, url, headers, payload, timeout):
        self.calls.append((url, json.dumps(payload, ensure_ascii=False)))
        if len(self.calls) == 1:
            await asyncio.to_thread(self.release.wait, 5)
        if self.kind in ("translation", "glossary"):
            return await self.echo.execute(url, headers, payload, timeout)
        return TransportResult(200, {"choices": [{"message": {"content": '{"decisions":[]}'}}]})


@pytest.mark.parametrize("kind", ["translation", "glossary", "glossary_review"])
@pytest.mark.parametrize("rapid", [False, True])
def test_live_glossary_switch_uses_new_model_and_prompt_for_remaining_requests(tmp_path, kind, rapid):
    transport = _BlockedTransport(kind)
    service = _service(tmp_path, transport=transport)
    first = _seed_inputs(service, tmp_path, kind)
    for name in ("second", "third"):
        service.profile_store.create(replace(first, id=name, base_url=f"https://{name}.test/v1"))
    prompt = replace(default_preset(PromptKind(kind)), id="new-prompt", system_prompt="NEW PROMPT", is_system=False)
    PromptPresetStore(service.prompts_cache_root / f"prompts.{kind}.json", PromptKind(kind)).save((prompt,))
    router = _router(service)
    router.call("workflow_presets.create", {"kind": kind, "preset": {
        "id": "new-preset", "name": "New", "model_profile_id": "second",
        "prompt_preset_id": prompt.id, "source_language": "kr", "target_language": "zh",
    }})
    task_id = getattr(service, f"start_{kind}")("start")["task_id"]
    try:
        _wait_until(lambda: len(transport.calls) == 1)
        router.call("workflow_presets.apply", {"kind": kind, "id": "new-preset"})
        assert service.cache.load_record(task_id).status is TaskStatus.STOPPING
        if rapid:
            router.call("model_profiles.select_active", {"module": kind, "profile_id": "third"})
    finally:
        transport.release.set()
    _wait_until(lambda: service.cache.load_record(task_id).status in (TaskStatus.COMPLETED, TaskStatus.FAILED))
    assert service.cache.load_record(task_id).status is TaskStatus.COMPLETED
    assert len(transport.calls) > 1
    expected = "third" if rapid else "second"
    assert all(f"https://{expected}.test/" in url for url, _ in transport.calls[1:])
    assert all("NEW PROMPT" in payload for _, payload in transport.calls[1:])
    snapshot = service.read_snapshot(kind=kind, task_id=task_id)["snapshot"]
    assert snapshot["active_model_id"] == expected
    assert snapshot["active_prompt_id"] == prompt.id


@pytest.mark.parametrize("kind", ["translation", "glossary", "glossary_review"])
@pytest.mark.parametrize("action", ["stop", "delete_model", "delete_prompt"])
def test_stop_or_delete_cancels_pending_switch_without_more_requests(tmp_path, kind, action):
    transport = _BlockedTransport(kind)
    service = _service(tmp_path, transport=transport)
    first = _seed_inputs(service, tmp_path, kind)
    service.profile_store.create(replace(first, id="second"))
    custom = replace(default_preset(PromptKind(kind)), id="second-prompt", system_prompt="new", is_system=False)
    PromptPresetStore(service.prompts_cache_root / f"prompts.{kind}.json", PromptKind(kind)).save((custom,))
    router = _router(service)
    task_id = getattr(service, f"start_{kind}")("start")["task_id"]
    try:
        _wait_until(lambda: len(transport.calls) == 1)
        router.call("model_profiles.select_active", {"module": kind, "profile_id": "second"})
        router.call("prompts.select_active", {"kind": kind, "preset_id": custom.id})
        if action == "stop":
            service.stop_task(kind=kind, task_id=task_id)
        elif action == "delete_model":
            router.call("model_profiles.delete", {"id": "second"})
        else:
            router.call("prompts.delete", {"id": custom.id})
        assert task_id not in service._selection_switch_pending
    finally:
        transport.release.set()
    _wait_until(lambda: service.registry.get(task_id).is_done)
    assert service.cache.load_record(task_id).status is TaskStatus.STOPPED
    assert len(transport.calls) == 1
    assert service.cache.load(task_id).progress().pending > 0


def _live_record(service, kind, profile, *, advanced=False):
    settings = getattr(service.settings_store.load_all(), kind)
    metadata = {
        "input_dir": settings.input_folder,
        "output_dir": settings.input_folder if kind == "glossary_review" else settings.output_folder,
        "source_language": settings.source_language, "target_language": settings.target_language,
        "model_id": profile.id, "prompt_preset_id": default_preset(PromptKind(kind)).id,
    }
    if advanced:
        metadata["advanced_routing"] = {"preset_id": "multi", "routes": [
            {"model": {"id": profile.id}, "prompt_preset": {"id": metadata["prompt_preset_id"]}},
            {"model": {"id": "helper"}, "prompt_preset": {"id": "helper-prompt"}},
        ]}
    task_id = f"{kind}-live-selection"
    service.cache.write_seed(TaskRecord(id=task_id, kind=TaskKind(kind), status=TaskStatus.RUNNING, metadata=metadata),
                             [Subtask(id="pending", task_id=task_id)])
    running = RunningTask(task_id=task_id, kind=kind, cache=service.cache, created_at="2026-09-29T00:00:00Z")
    service.registry.add(running)
    return task_id, running


@pytest.mark.parametrize("kind", ["translation", "glossary", "glossary_review"])
@pytest.mark.parametrize("resource", ["model", "prompt"])
def test_deleting_active_config_stops_affected_task(tmp_path, kind, resource):
    service = _service(tmp_path, transport=EchoTranslationTransport())
    profile = _seed_inputs(service, tmp_path, kind)
    custom = replace(default_preset(PromptKind(kind)), id="custom", is_system=False)
    PromptPresetStore(service.prompts_cache_root / f"prompts.{kind}.json", PromptKind(kind)).save((custom,))
    service.settings_store.save_partial("app", {f"active_{kind}_prompt_id": custom.id})
    task_id, running = _live_record(service, kind, profile)
    record = service.cache.load_record(task_id)
    service.cache.save_task(replace(record, metadata={**record.metadata, "prompt_preset_id": custom.id}))
    router = _router(service)
    if resource == "model":
        router.call("model_profiles.delete", {"id": profile.id})
    else:
        router.call("prompts.delete", {"id": custom.id})
    assert running.stop_requested
    assert service.cache.load_record(task_id).status is TaskStatus.STOPPING
    assert task_id not in service._selection_switch_pending


@pytest.mark.parametrize("resource", ["model", "prompt"])
def test_deleting_secondary_route_stops_task_but_unrelated_resource_does_not(tmp_path, resource):
    service = _service(tmp_path, transport=EchoTranslationTransport())
    profile = _seed_inputs(service, tmp_path, "translation")
    for name in ("helper", "unrelated"):
        service.profile_store.create(replace(profile, id=name))
    prompts = [replace(default_preset(PromptKind.TRANSLATION), id=name, is_system=False)
               for name in ("helper-prompt", "unrelated-prompt")]
    PromptPresetStore(service.prompts_cache_root / "prompts.translation.json", PromptKind.TRANSLATION).save(prompts)
    task_id, running = _live_record(service, "translation", profile, advanced=True)
    router = _router(service)
    method = "model_profiles.delete" if resource == "model" else "prompts.delete"
    router.call(method, {"id": "unrelated" if resource == "model" else "unrelated-prompt"})
    assert not running.stop_requested
    router.call(method, {"id": "helper" if resource == "model" else "helper-prompt"})
    assert running.stop_requested
    assert service.cache.load_record(task_id).status is TaskStatus.STOPPING


@pytest.mark.parametrize("kind", ["translation", "glossary", "glossary_review"])
def test_incompatible_preset_reverts_settings_without_stopping_live_task(tmp_path, kind):
    service = _service(tmp_path, transport=EchoTranslationTransport())
    profile = _seed_inputs(service, tmp_path, kind)
    task_id, running = _live_record(service, kind, profile)
    router = _router(service)
    router.call("workflow_presets.create", {"kind": kind, "preset": {
        "id": "other-language", "name": "Other", "model_profile_id": profile.id,
        "prompt_preset_id": default_preset(PromptKind(kind)).id,
        "source_language": "ja", "target_language": "zh",
    }})
    before = service.settings_store.load_all()
    with pytest.raises(BridgeError) as caught:
        router.call("workflow_presets.apply", {"kind": kind, "id": "other-language"})
    assert caught.value.payload.message_key == "task.selection_incompatible"
    assert service.settings_store.load_all() == before
    assert not running.stop_requested
    assert service.cache.load_record(task_id).status is TaskStatus.RUNNING


@pytest.mark.parametrize("kind", ["translation", "glossary", "glossary_review"])
def test_delete_cancels_switch_after_old_thread_has_finished(tmp_path, kind):
    service = _service(tmp_path, transport=EchoTranslationTransport())
    profile = _seed_inputs(service, tmp_path, kind)
    service.profile_store.create(replace(profile, id="second"))
    task_id, running = _live_record(service, kind, profile)
    service.settings_store.save_partial("app", {f"active_{kind}_model_id": "second"})
    service._selection_switch_pending[task_id] = service._build_selection_config(
        kind, service.cache.load_record(task_id)
    )
    service._mark_status(task_id, TaskStatus.STOPPED)
    running.mark_done()

    _router(service).call("model_profiles.delete", {"id": "second"})

    assert task_id not in service._selection_switch_pending
    assert service.cache.load_record(task_id).status is TaskStatus.STOPPED


def test_deleting_model_does_not_change_completed_task_state(tmp_path):
    service = _service(tmp_path, transport=EchoTranslationTransport())
    profile = _seed_inputs(service, tmp_path, "translation")
    task_id, _ = _live_record(service, "translation", profile)
    service._mark_status(task_id, TaskStatus.COMPLETED)
    _router(service).call("model_profiles.delete", {"id": profile.id})
    assert service.cache.load_record(task_id).status is TaskStatus.COMPLETED


def test_deleting_active_advanced_preset_resumes_remaining_work_as_single_model(tmp_path, monkeypatch):
    transport = _BlockedTransport("translation")
    service = _service(tmp_path, transport=transport)
    profile = _seed_inputs(service, tmp_path, "translation")
    router = _router(service)
    prompt_id = default_preset(PromptKind.TRANSLATION).id
    router.call("workflow_presets.create", {"kind": "translation", "preset": {
        "id": "multi", "name": "Multi", "model_profile_id": profile.id,
        "prompt_preset_id": prompt_id, "source_language": "kr", "target_language": "zh",
        "advanced": True, "group_concurrency": 1,
        "routes": [{"model_profile_id": profile.id, "prompt_preset_id": prompt_id}],
    }})
    router.call("workflow_presets.apply", {"kind": "translation", "id": "multi"})
    task_id = service.start_translation("start")["task_id"]
    try:
        _wait_until(lambda: len(transport.calls) == 1)
        router.call("workflow_presets.delete", {"id": "multi"})
    finally:
        transport.release.set()
    _wait_until(lambda: service.cache.load_record(task_id).status is TaskStatus.COMPLETED)
    assert len(transport.calls) > 1
    assert "advanced_routing" not in service.cache.load_record(task_id).metadata


def test_invalidated_selection_cancels_finished_thread_pending_resume(tmp_path):
    service = _service(tmp_path, transport=EchoTranslationTransport())
    profile = _seed_inputs(service, tmp_path, "translation")
    task_id, running = _live_record(service, "translation", profile)
    service._selection_switch_pending[task_id] = service._build_selection_config(
        "translation", service.cache.load_record(task_id)
    )
    service._mark_status(task_id, TaskStatus.STOPPED)
    running.mark_done()
    service.settings_store.save_partial("translation", {"source_language": "ja"})

    service.selection_invalidated("translation")

    assert task_id not in service._selection_switch_pending
    assert service.cache.load_record(task_id).status is TaskStatus.STOPPED
