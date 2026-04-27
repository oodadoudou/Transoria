"""Tests for ``transoria.bridge.handlers.tasks`` snapshot stubs."""

from __future__ import annotations

import pytest

from transoria.bridge import BridgeError, build_default_router

DOMAINS = ("translation", "glossary", "replacement")


@pytest.fixture(scope="module")
def router():
    return build_default_router()


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
