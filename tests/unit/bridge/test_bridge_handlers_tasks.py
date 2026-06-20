"""Tests for ``transoria.bridge.handlers.tasks`` snapshot stubs."""

from __future__ import annotations

import pytest

from transoria.bridge import BridgeError, build_default_router

DOMAINS = (
    "translation",
    "glossary",
    "replacement",
    "epub_compress",
    "epub_merge",
    "epub_convert",
)


@pytest.fixture
def router(tmp_path):
    return build_default_router(cache_root=tmp_path)


@pytest.mark.parametrize("domain", DOMAINS)
def test_list_recent_tasks_returns_empty(router, domain):
    response = router.call(f"{domain}.list_recent_tasks", {})

    assert response == {"tasks": []}


@pytest.mark.parametrize("domain", DOMAINS)
def test_read_snapshot_unknown_task_raises_not_found(router, domain):
    with pytest.raises(BridgeError) as caught:
        router.call(f"{domain}.read_snapshot", {"task_id": "missing"})

    assert caught.value.code == "bridge.not_found"
    assert caught.value.payload.details["task_id"] == "missing"


@pytest.mark.parametrize("domain", DOMAINS)
def test_read_snapshot_requires_task_id(router, domain):
    with pytest.raises(BridgeError) as caught:
        router.call(f"{domain}.read_snapshot", {})

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "task_id"


@pytest.mark.parametrize("domain", DOMAINS)
def test_list_failed_subtasks_unknown_task_raises_not_found(router, domain):
    with pytest.raises(BridgeError) as caught:
        router.call(f"{domain}.list_failed_subtasks", {"task_id": "missing"})

    assert caught.value.code == "bridge.not_found"


def test_default_router_registers_all_task_methods():
    methods = build_default_router().methods()

    for domain in DOMAINS:
        assert f"{domain}.list_recent_tasks" in methods
        assert f"{domain}.read_snapshot" in methods
        assert f"{domain}.list_failed_subtasks" in methods


def test_translation_lifecycle_methods_registered():
    methods = build_default_router().methods()

    for method in (
        "translation.start_task",
        "translation.stop_task",
        "translation.pause_task",
        "translation.continue_task",
        "translation.read_artifacts",
        "glossary.start_task",
        "glossary.stop_task",
        "glossary.pause_task",
        "glossary.continue_task",
        "glossary.read_artifacts",
        "replacement.start_task",
        "replacement.stop_task",
        "replacement.read_artifacts",
        "replacement.read_snapshot",
        "replacement.list_recent_tasks",
        "replacement.list_failed_subtasks",
        "epub_compress.preview",
        "epub_compress.start_task",
        "epub_compress.stop_task",
        "epub_compress.read_artifacts",
        "epub_compress.read_snapshot",
        "epub_compress.list_recent_tasks",
        "epub_compress.list_failed_subtasks",
        "epub_merge.preview",
        "epub_merge.start_task",
        "epub_merge.stop_task",
        "epub_merge.read_artifacts",
        "epub_merge.read_snapshot",
        "epub_merge.list_recent_tasks",
        "epub_merge.list_failed_subtasks",
        "epub_convert.preview",
        "epub_convert.start_task",
        "epub_convert.stop_task",
        "epub_convert.read_artifacts",
        "epub_convert.read_snapshot",
        "epub_convert.list_recent_tasks",
        "epub_convert.list_failed_subtasks",
    ):
        assert method in methods, method


def test_translation_pause_unknown_task_returns_not_found(router):
    """Pause is now wired (D.1); calling it on an unknown id surfaces
    ``bridge.not_found`` rather than the legacy ``not_supported``."""

    with pytest.raises(BridgeError) as caught:
        router.call("translation.pause_task", {"task_id": "t-missing"})
    assert caught.value.code == "bridge.not_found"


def test_translation_continue_unknown_task_returns_not_found(router):
    with pytest.raises(BridgeError) as caught:
        router.call("translation.continue_task", {"task_id": "t-missing"})
    assert caught.value.code == "bridge.not_found"


def test_replacement_start_task_validates_rules(router):
    with pytest.raises(BridgeError) as caught:
        router.call("replacement.start_task", {"request_id": "r"})
    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "rules"


def test_epub_merge_start_allows_blank_output_path(router):
    with pytest.raises(BridgeError) as caught:
        router.call(
            "epub_merge.start_task",
            {
                "request_id": "r",
                "input_dir": "/nonexistent-folder",
                "output_path": "",
                "options": {},
                "actions": [],
            },
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "actions"
