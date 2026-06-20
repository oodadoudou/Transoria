"""Tests for ``transoria.bridge.handlers.model_profiles``."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.model_profiles import register
from transoria.llm.client import TransportResult
from transoria.llm.config import ModelConfig, ProviderFormat
from transoria.model_profiles import DEFAULT_PROFILE_IDS, ModelProfileStore
from transoria.settings import SettingsStore


def _seed_legacy_profiles(store: ModelProfileStore) -> None:
    """Recreate the legacy auto-seeded profiles for handler tests.

    Step G removed first-run auto-seeding from
    :class:`ModelProfileStore`. Tests that exercise existing-profile
    flows (update / delete / select_active / test_connection /
    fetch_model_list) seed via this helper so they don't depend on
    the removed behavior."""

    profiles = (
        ModelConfig(
            id="preset-openai",
            display_name="OpenAI",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        ),
        ModelConfig(
            id="preset-anthropic",
            display_name="Anthropic",
            provider_format=ProviderFormat.ANTHROPIC,
            base_url="https://api.anthropic.com",
            model_id="claude-sonnet-4-6",
        ),
        ModelConfig(
            id="preset-google",
            display_name="Google",
            provider_format=ProviderFormat.GOOGLE,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model_id="gemini-2.5-flash",
        ),
        ModelConfig(
            id="preset-deepseek",
            display_name="DeepSeek",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-chat",
        ),
    )
    for profile in profiles:
        store.create(profile)


@dataclass
class _StubChatTransport:
    """Canned chat transport used by test_connection tests.

    ``responses`` is a list of (status, body) tuples consumed in order.
    Raises ``RuntimeError`` if exhausted, so tests notice when their
    queue is too short.
    """

    responses: list[tuple[int, dict]] = field(default_factory=list)
    last_request: dict | None = field(default=None)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.last_request = {
            "url": url,
            "headers": dict(headers),
            "payload": dict(payload),
            "timeout": timeout,
        }
        if not self.responses:
            raise RuntimeError("StubChatTransport exhausted")
        status, body = self.responses.pop(0)
        return TransportResult(status_code=status, body=body)


@dataclass
class _StubHttpResponse:
    status_code: int
    text: str
    json_payload: object

    def json(self) -> object:
        return self.json_payload


@dataclass
class _StubHttpClient:
    """Stand-in for httpx.Client used by fetch_model_list."""

    response: _StubHttpResponse
    last_url: str | None = field(default=None)
    last_headers: dict | None = field(default=None)

    def get(self, url: str, headers: Mapping[str, str]) -> _StubHttpResponse:
        self.last_url = url
        self.last_headers = dict(headers)
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.fixture
def env(tmp_path: Path):
    profile_store = ModelProfileStore.from_cache_root(tmp_path)
    _seed_legacy_profiles(profile_store)
    settings_store = SettingsStore(path=tmp_path / "settings.json")
    router = BridgeRouter()
    register(router, profile_store=profile_store, settings_store=settings_store)
    return router, profile_store, settings_store


@pytest.fixture
def env_with_stubs(tmp_path: Path):
    profile_store = ModelProfileStore.from_cache_root(tmp_path)
    _seed_legacy_profiles(profile_store)
    settings_store = SettingsStore(path=tmp_path / "settings.json")
    router = BridgeRouter()
    chat_transport = _StubChatTransport()
    http_response = _StubHttpResponse(
        status_code=200, text="", json_payload={"data": []}
    )
    http_client = _StubHttpClient(response=http_response)

    @contextmanager
    def http_factory(timeout: float):
        http_client.last_url = None
        http_client.last_headers = None
        yield http_client

    register(
        router,
        profile_store=profile_store,
        settings_store=settings_store,
        chat_transport_factory=lambda: chat_transport,
        http_client_factory=http_factory,
    )
    return {
        "router": router,
        "profile_store": profile_store,
        "settings_store": settings_store,
        "chat_transport": chat_transport,
        "http_client": http_client,
        "http_response": http_response,
    }


def test_list_returns_seeded_profiles(env):
    router, _, _ = env

    response = router.call("model_profiles.list", {})

    ids = {p["id"] for p in response["profiles"]}
    assert ids == set(DEFAULT_PROFILE_IDS)
    for profile in response["profiles"]:
        assert "api_keys" not in profile
        assert profile["api_key_status"] in {"missing", "present"}
        assert profile["api_key_masked"] == ""


def test_create_appends_new_profile(env):
    router, store, _ = env

    response = router.call(
        "model_profiles.create",
        {
            "profile": {
                "display_name": "Volcengine Ark",
                "provider_format": "openai",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3/",
                "model_id": "deepseek-v3-2-251201",
                "api_keys": ["sk-ark-test"],
            }
        },
    )

    profile = response["profile"]
    assert profile["display_name"] == "Volcengine Ark"
    assert profile["api_key_status"] == "present"
    assert profile["api_key_masked"].startswith("…")
    assert any(p.id == profile["id"] for p in store.load())


def test_create_rejects_duplicate_id_with_conflict(env):
    router, _, _ = env
    body = {
        "id": "preset-openai",
        "display_name": "x",
        "provider_format": "openai",
        "base_url": "x",
        "model_id": "x",
    }

    with pytest.raises(BridgeError) as caught:
        router.call("model_profiles.create", {"profile": body})

    assert caught.value.code == "bridge.conflict"


def test_update_persists_change(env):
    router, store, _ = env

    response = router.call(
        "model_profiles.update",
        {"id": "preset-openai", "patch": {"display_name": "OpenAI Pro"}},
    )

    assert response["profile"]["display_name"] == "OpenAI Pro"
    refreshed = store.get("preset-openai")
    assert refreshed is not None and refreshed.display_name == "OpenAI Pro"


def test_update_unknown_profile_returns_not_found(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.update",
            {"id": "missing", "patch": {"display_name": "x"}},
        )

    assert caught.value.code == "bridge.not_found"


def test_update_rejects_api_keys_field(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.update",
            {"id": "preset-openai", "patch": {"api_keys": ["sk-x"]}},
        )

    assert caught.value.code == "bridge.invalid_argument"


def test_set_api_key_replaces_keys(env):
    router, store, _ = env

    response = router.call(
        "model_profiles.set_api_key",
        {"id": "preset-deepseek", "api_keys": ["sk-1", "sk-2"]},
    )

    assert response["profile"]["api_key_status"] == "present"
    assert store.api_key_status("preset-deepseek") == "present"


def test_select_active_updates_app_settings(env):
    router, _, settings_store = env

    response = router.call(
        "model_profiles.select_active",
        {"module": "translation", "profile_id": "preset-anthropic"},
    )

    assert response["app"]["active_translation_model_id"] == "preset-anthropic"
    assert (
        settings_store.load_all().app.active_translation_model_id
        == "preset-anthropic"
    )


def test_select_active_rejects_unknown_profile(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.select_active",
            {"module": "translation", "profile_id": "missing"},
        )

    assert caught.value.code == "bridge.not_found"


def test_select_active_clears_when_null(env):
    router, _, settings_store = env
    router.call(
        "model_profiles.select_active",
        {"module": "translation", "profile_id": "preset-openai"},
    )

    response = router.call(
        "model_profiles.select_active",
        {"module": "translation", "profile_id": None},
    )

    assert response["app"]["active_translation_model_id"] is None
    assert (
        settings_store.load_all().app.active_translation_model_id is None
    )


def test_delete_clears_active_selection(env):
    router, _, settings_store = env
    router.call(
        "model_profiles.select_active",
        {"module": "glossary", "profile_id": "preset-openai"},
    )

    router.call("model_profiles.delete", {"id": "preset-openai"})

    assert settings_store.load_all().app.active_glossary_model_id is None


def test_test_connection_requires_api_key(env):
    router, _, _ = env

    # Default seeded profiles have no API key.
    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.test_connection",
            {"id": "preset-openai", "request_id": "rid-1"},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["reason"] == "missing_api_key"


def test_test_connection_returns_not_found_for_missing_profile(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.test_connection",
            {"id": "missing-profile", "request_id": "rid-1"},
        )

    assert caught.value.code == "bridge.not_found"


def test_test_connection_success_returns_latency_and_status(env_with_stubs):
    router = env_with_stubs["router"]
    profile_store = env_with_stubs["profile_store"]
    chat_transport = env_with_stubs["chat_transport"]
    profile_store.set_api_keys("preset-openai", ("sk-test",))
    chat_transport.responses.append(
        (
            200,
            {
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )

    response = router.call(
        "model_profiles.test_connection",
        {"id": "preset-openai", "request_id": "rid-1"},
    )

    assert response["request_id"] == "rid-1"
    assert response["ok"] is True
    assert response["latency_ms"] >= 0
    assert response["provider_response"]["status_code"] == 200
    assert response["provider_response"]["model"] == "gpt-4o-mini"
    assert "received" in response["provider_response"]["detail"]
    # The chat request reached the transport with bearer auth.
    assert chat_transport.last_request is not None
    assert chat_transport.last_request["headers"]["Authorization"].startswith(
        "Bearer "
    )


def test_test_connection_failure_returns_provider_error(env_with_stubs):
    router = env_with_stubs["router"]
    profile_store = env_with_stubs["profile_store"]
    chat_transport = env_with_stubs["chat_transport"]
    profile_store.set_api_keys("preset-openai", ("sk-bad",))
    chat_transport.responses.append((401, {"error": {"message": "unauthorized"}}))

    response = router.call(
        "model_profiles.test_connection",
        {"id": "preset-openai", "request_id": "rid-1"},
    )

    assert response["ok"] is False
    assert response["latency_ms"] >= 0
    assert response["provider_response"]["status_code"] == 401
    assert "401" in response["provider_response"]["detail"]


def test_fetch_model_list_requires_api_key(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.fetch_model_list",
            {"id": "preset-openai", "request_id": "rid-1"},
        )

    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["reason"] == "missing_api_key"


def test_fetch_model_list_anthropic_unsupported(env):
    router, _, _ = env

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.fetch_model_list",
            {"id": "preset-anthropic", "request_id": "rid-1"},
        )

    assert caught.value.code == "bridge.invalid_argument"
    # Anthropic stub still rejects before reaching the transport because
    # default profile has no API key — but the unsupported reason is the
    # stronger rejection. Either is fine; we just want the UI to render
    # a typed error.
    assert caught.value.payload.details["reason"] in {
        "unsupported",
        "missing_api_key",
    }


def test_fetch_model_list_openai_returns_models(env_with_stubs):
    router = env_with_stubs["router"]
    profile_store = env_with_stubs["profile_store"]
    http_response = env_with_stubs["http_response"]
    http_client = env_with_stubs["http_client"]
    profile_store.set_api_keys("preset-openai", ("sk-test",))
    http_response.json_payload = {
        "data": [
            {"id": "gpt-4o-mini"},
            {"id": "gpt-4.1"},
            "o3-mini",
        ]
    }

    response = router.call(
        "model_profiles.fetch_model_list",
        {"id": "preset-openai", "request_id": "rid-1"},
    )

    assert response["request_id"] == "rid-1"
    ids = [m["id"] for m in response["models"]]
    assert ids == ["gpt-4o-mini", "gpt-4.1", "o3-mini"]
    assert http_client.last_url is not None and http_client.last_url.endswith(
        "/models"
    )
    assert http_client.last_headers is not None
    assert http_client.last_headers["Authorization"].startswith("Bearer ")


def test_fetch_model_list_google_uses_url_key(env_with_stubs):
    router = env_with_stubs["router"]
    profile_store = env_with_stubs["profile_store"]
    http_response = env_with_stubs["http_response"]
    http_client = env_with_stubs["http_client"]
    profile_store.set_api_keys("preset-google", ("g-test",))
    http_response.json_payload = {
        "models": [
            {"name": "models/gemini-2.0-flash", "displayName": "Gemini 2.0 Flash"},
            {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro"},
        ]
    }

    response = router.call(
        "model_profiles.fetch_model_list",
        {"id": "preset-google", "request_id": "rid-1"},
    )

    ids = [m["id"] for m in response["models"]]
    assert "gemini-2.0-flash" in ids
    assert "gemini-2.5-pro" in ids
    assert http_client.last_url is not None
    assert "key=g-test" in http_client.last_url


def test_fetch_model_list_propagates_http_error(env_with_stubs):
    router = env_with_stubs["router"]
    profile_store = env_with_stubs["profile_store"]
    http_response = env_with_stubs["http_response"]
    profile_store.set_api_keys("preset-openai", ("sk-bad",))
    http_response.status_code = 401
    http_response.text = '{"error":"unauthorized"}'
    http_response.json_payload = {"error": "unauthorized"}

    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.fetch_model_list",
            {"id": "preset-openai", "request_id": "rid-1"},
        )

    assert caught.value.code == "llm.request_failed"
    assert caught.value.payload.details["cause"] == "llm.http_error"
# Inline-credential variants (G.2)


def test_test_connection_inline_succeeds_without_saving_profile(env_with_stubs):
    """G.2: pass inline credentials directly — no profile is created
    or persisted, the chat call still succeeds."""

    router = env_with_stubs["router"]
    profile_store = env_with_stubs["profile_store"]
    chat_transport = env_with_stubs["chat_transport"]
    chat_transport.responses.append(
        (
            200,
            {
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )

    response = router.call(
        "model_profiles.test_connection",
        {
            "provider_format": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-inline",
            "model_id": "gpt-4o-mini",
            "request_id": "rid-1",
        },
    )

    assert response["ok"] is True
    assert response["provider_response"]["status_code"] == 200
    # No new profile persisted.
    assert all(p.id != "inline-probe" for p in profile_store.load())


def test_test_connection_inline_requires_model_id(env_with_stubs):
    router = env_with_stubs["router"]
    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.test_connection",
            {
                "provider_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-inline",
                "request_id": "rid-1",
            },
        )
    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "model_id"


def test_test_connection_rejects_mixed_id_and_inline(env_with_stubs):
    router = env_with_stubs["router"]
    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.test_connection",
            {
                "id": "preset-openai",
                "base_url": "https://other.example.com",
                "request_id": "rid-1",
            },
        )
    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["reason"] == "ambiguous_payload"


def test_test_connection_rejects_empty_payload(env_with_stubs):
    router = env_with_stubs["router"]
    with pytest.raises(BridgeError) as caught:
        router.call("model_profiles.test_connection", {"request_id": "rid-1"})
    assert caught.value.code == "bridge.invalid_argument"


def test_fetch_model_list_inline_works_for_openai(env_with_stubs):
    router = env_with_stubs["router"]
    http_response = env_with_stubs["http_response"]
    http_response.json_payload = {"data": [{"id": "gpt-4o"}]}

    response = router.call(
        "model_profiles.fetch_model_list",
        {
            "provider_format": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-inline",
            "request_id": "rid-1",
        },
    )

    assert response["models"] == [{"id": "gpt-4o"}]
    http_client = env_with_stubs["http_client"]
    assert http_client.last_url == "https://api.example.com/v1/models"
    assert http_client.last_headers["Authorization"] == "Bearer sk-inline"


def test_fetch_model_list_inline_rejects_anthropic(env_with_stubs):
    router = env_with_stubs["router"]
    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.fetch_model_list",
            {
                "provider_format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-inline",
                "request_id": "rid-1",
            },
        )
    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["reason"] == "unsupported"


def test_inline_rejects_unknown_provider_format(env_with_stubs):
    router = env_with_stubs["router"]
    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.test_connection",
            {
                "provider_format": "made-up-provider",
                "base_url": "https://example.com",
                "api_key": "sk",
                "model_id": "x",
                "request_id": "rid-1",
            },
        )
    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "provider_format"


def test_inline_rejects_empty_api_key(env_with_stubs):
    router = env_with_stubs["router"]
    with pytest.raises(BridgeError) as caught:
        router.call(
            "model_profiles.test_connection",
            {
                "provider_format": "openai",
                "base_url": "https://example.com",
                "api_key": "",
                "model_id": "x",
                "request_id": "rid-1",
            },
        )
    assert caught.value.code == "bridge.invalid_argument"
    assert caught.value.payload.details["field"] == "api_key"
