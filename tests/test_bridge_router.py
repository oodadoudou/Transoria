"""Tests for ``transoria.bridge.router``."""

from __future__ import annotations

from typing import Mapping

import pytest

from transoria.bridge import BridgeError, BridgeRouter


def _echo(payload: Mapping[str, object]) -> Mapping[str, object]:
    return {"echo": dict(payload)}


def test_router_registers_and_dispatches_known_method():
    router = BridgeRouter()
    router.register("test.echo", _echo)

    response = router.dispatch("test.echo", {"value": 42})

    assert response == {"echo": {"value": 42}}


def test_router_rejects_duplicate_registration():
    router = BridgeRouter()
    router.register("test.echo", _echo)

    with pytest.raises(ValueError):
        router.register("test.echo", _echo)


def test_router_unknown_method_raises_bridge_not_found():
    router = BridgeRouter()

    with pytest.raises(BridgeError) as caught:
        router.dispatch("missing.method")

    assert caught.value.code == "bridge.not_found"
    assert caught.value.payload.details["method"] == "missing.method"


def test_router_payload_must_be_mapping():
    router = BridgeRouter()
    router.register("test.echo", _echo)

    with pytest.raises(BridgeError) as caught:
        router.dispatch("test.echo", payload="not-a-dict")  # type: ignore[arg-type]

    assert caught.value.code == "bridge.invalid_argument"


def test_router_call_wraps_unexpected_exceptions():
    def explode(_payload: Mapping[str, object]) -> Mapping[str, object]:
        raise RuntimeError("boom")

    router = BridgeRouter()
    router.register("test.boom", explode)

    with pytest.raises(BridgeError) as caught:
        router.call("test.boom", {})

    assert caught.value.code == "bridge.io_error"
    assert caught.value.payload.details["exception"] == "RuntimeError"


def test_router_call_propagates_bridge_errors_unchanged():
    def reject(_payload: Mapping[str, object]) -> Mapping[str, object]:
        raise BridgeError.invalid_argument("missing field", field="path")

    router = BridgeRouter()
    router.register("test.reject", reject)

    with pytest.raises(BridgeError) as caught:
        router.call("test.reject", {})

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "path"


def test_methods_listing_is_sorted():
    router = BridgeRouter()
    router.register("b.method", _echo)
    router.register("a.method", _echo)

    assert router.methods() == ("a.method", "b.method")


def test_bridge_error_payload_dict_serialization():
    err = BridgeError(
        "task.invalid_transition",
        "cannot pause a stopped task",
        retryable=False,
        message_key="task.invalid_transition",
        details={"task_id": "abc"},
    )

    payload = err.payload.to_dict()

    assert payload == {
        "code": "task.invalid_transition",
        "message": "cannot pause a stopped task",
        "retryable": False,
        "message_key": "task.invalid_transition",
        "details": {"task_id": "abc"},
    }
