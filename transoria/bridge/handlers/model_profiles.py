"""``model_profiles.*`` bridge handlers.

Profiles persist via :class:`ModelProfileStore`. API keys live in a
separate file and never appear in the profile body returned to the
frontend — only ``api_key_status`` and a masked tail.

``test_connection`` issues a minimal LLM call (max 1 output token) using
the configured profile and returns latency + status, so users can verify
their API key + base URL + provider format are correct without leaving
the Model page.

``fetch_model_list`` queries the provider's ``/models`` endpoint where
supported (OpenAI-compatible, Google) so users can pick a model_id from a
live dropdown instead of typing it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Mapping

import httpx

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.llm.client import (
    ChatRequest,
    ChatTransport,
    HttpxChatTransport,
    LlmClient,
    LlmRequestError,
)
from transoria.llm.config import ModelConfig, ProviderFormat, ThinkingLevel
from transoria.model_profiles import ModelProfileStore, mask_api_keys
from transoria.settings import SettingsStore

ACTIVE_FIELD_BY_MODULE = {
    "translation": "active_translation_model_id",
    "glossary": "active_glossary_model_id",
    "glossary_review": "active_glossary_review_model_id",
}


def _profile_to_dict(profile: ModelConfig, status: str) -> dict[str, object]:
    body = profile.to_dict()
    body.pop("api_keys", None)
    body["api_key_status"] = status
    body["api_key_masked"] = mask_api_keys(profile.api_keys)
    return body


def _build_handlers(
    profile_store: ModelProfileStore,
    settings_store: SettingsStore,
    *,
    chat_transport_factory,
    http_client_factory,
) -> dict[str, object]:
    def list_profiles(_payload: Mapping[str, object]) -> dict[str, object]:
        profiles = profile_store.load()
        return {
            "profiles": [
                _profile_to_dict(p, profile_store.api_key_status(p.id))
                for p in profiles
            ]
        }

    def read_full(payload: Mapping[str, object]) -> dict[str, object]:
        profile_id = expect_string(payload, "id")
        profiles = profile_store.load()
        match = next((p for p in profiles if p.id == profile_id), None)
        if match is None:
            raise BridgeError.not_found(
                f"profile not found: {profile_id!r}",
                details={"id": profile_id},
            )
        body = _profile_to_dict(match, profile_store.api_key_status(profile_id))
        return {"profile": body, "api_keys": list(match.api_keys)}

    def create(payload: Mapping[str, object]) -> dict[str, object]:
        body = payload.get("profile")
        if not isinstance(body, Mapping):
            raise BridgeError.invalid_argument(
                "profile object is required.",
                field="profile",
            )
        try:
            profile = _profile_from_payload(body)
        except (KeyError, ValueError) as exc:
            raise BridgeError.invalid_argument(
                str(exc),
                field=_field_from_exc(exc),
            ) from exc
        try:
            stored = profile_store.create(profile)
        except ValueError as exc:
            raise BridgeError.conflict(str(exc)) from exc
        return {
            "profile": _profile_to_dict(
                stored, profile_store.api_key_status(stored.id)
            )
        }

    def update(payload: Mapping[str, object]) -> dict[str, object]:
        profile_id = expect_string(payload, "id")
        patch = payload.get("patch")
        if not isinstance(patch, Mapping):
            raise BridgeError.invalid_argument(
                "patch object is required.",
                field="patch",
            )
        coerced = _coerce_patch(patch)
        try:
            stored = profile_store.update(profile_id, coerced)
        except KeyError as exc:
            raise BridgeError.not_found(
                f"profile {profile_id!r} does not exist."
            ) from exc
        except ValueError as exc:
            raise BridgeError.invalid_argument(
                str(exc),
                field=_field_from_exc(exc),
            ) from exc
        return {
            "profile": _profile_to_dict(
                stored, profile_store.api_key_status(stored.id)
            )
        }

    def delete(payload: Mapping[str, object]) -> dict[str, object]:
        profile_id = expect_string(payload, "id")
        try:
            profile_store.delete(profile_id)
        except KeyError as exc:
            raise BridgeError.not_found(
                f"profile {profile_id!r} does not exist."
            ) from exc
        # Clear active-model selections that referenced the deleted profile.
        current = settings_store.load_all()
        patch: dict[str, object] = {}
        if current.app.active_translation_model_id == profile_id:
            patch["active_translation_model_id"] = None
        if current.app.active_glossary_model_id == profile_id:
            patch["active_glossary_model_id"] = None
        if current.app.active_glossary_review_model_id == profile_id:
            patch["active_glossary_review_model_id"] = None
        if patch:
            settings_store.save_partial("app", patch)
        return {}

    def set_api_key(payload: Mapping[str, object]) -> dict[str, object]:
        profile_id = expect_string(payload, "id")
        keys = payload.get("api_keys")
        if not isinstance(keys, list):
            raise BridgeError.invalid_argument(
                "api_keys must be a list of strings.",
                field="api_keys",
            )
        try:
            stored = profile_store.set_api_keys(
                profile_id, [str(k) for k in keys]
            )
        except KeyError as exc:
            raise BridgeError.not_found(
                f"profile {profile_id!r} does not exist."
            ) from exc
        return {
            "profile": _profile_to_dict(
                stored, profile_store.api_key_status(stored.id)
            )
        }

    def select_active(payload: Mapping[str, object]) -> dict[str, object]:
        module = payload.get("module")
        if module not in ACTIVE_FIELD_BY_MODULE:
            raise BridgeError.invalid_argument(
                "module must be 'translation', 'glossary', or 'glossary_review'.",
                field="module",
            )
        profile_id = payload.get("profile_id")
        if profile_id is not None and not isinstance(profile_id, str):
            raise BridgeError.invalid_argument(
                "profile_id must be a string or null.",
                field="profile_id",
            )
        if isinstance(profile_id, str) and profile_id:
            if profile_store.get(profile_id) is None:
                raise BridgeError.not_found(
                    f"profile {profile_id!r} does not exist."
                )
        field = ACTIVE_FIELD_BY_MODULE[module]
        updated = settings_store.save_partial("app", {field: profile_id})
        return {"app": _app_settings_dict(updated.app)}

    def test_connection(payload: Mapping[str, object]) -> dict[str, object]:
        request_id = expect_string(payload, "request_id")
        profile = _resolve_profile_for_probe(
            payload,
            profile_store=profile_store,
            require_model_id=True,
            usage="testing",
        )
        client = LlmClient(transport=chat_transport_factory())
        request = ChatRequest(
            model=profile,
            system_prompt="",
            user_prompt="ping",
            temperature=0.0,
            stream=False,
        )
        start = time.monotonic()
        try:
            response = asyncio.run(client.chat(request))
        except LlmRequestError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "request_id": request_id,
                "ok": False,
                "latency_ms": elapsed,
                "provider_response": {
                    "model": profile.model_id,
                    "status_code": _status_code_from_error(exc),
                    "detail": f"[{exc.code}] {exc}",
                },
            }
        except Exception as exc:  # noqa: BLE001 — surface unexpected failures
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "request_id": request_id,
                "ok": False,
                "latency_ms": elapsed,
                "provider_response": {
                    "model": profile.model_id,
                    "status_code": None,
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            }
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "request_id": request_id,
            "ok": True,
            "latency_ms": elapsed,
            "provider_response": {
                "model": profile.model_id,
                "status_code": 200,
                "detail": (
                    f"received {len(response.content)} chars; "
                    f"in={response.usage.input_tokens} out={response.usage.output_tokens}"
                ),
            },
        }

    def fetch_model_list(payload: Mapping[str, object]) -> dict[str, object]:
        request_id = expect_string(payload, "request_id")
        profile = _resolve_profile_for_probe(
            payload,
            profile_store=profile_store,
            require_model_id=False,
            usage="fetching",
        )
        if profile.provider_format is ProviderFormat.ANTHROPIC:
            raise BridgeError.invalid_argument(
                "Anthropic does not expose a /models endpoint.",
                details={"reason": "unsupported", "provider": "anthropic"},
            )

        try:
            entries = _do_fetch_models(profile, http_client_factory)
        except LlmRequestError as exc:
            raise BridgeError(
                "llm.request_failed",
                str(exc),
                retryable=False,
                details={"cause": exc.code},
            ) from exc
        return {"request_id": request_id, "models": entries}

    return {
        "model_profiles.list": list_profiles,
        "model_profiles.read_full": read_full,
        "model_profiles.create": create,
        "model_profiles.update": update,
        "model_profiles.delete": delete,
        "model_profiles.set_api_key": set_api_key,
        "model_profiles.select_active": select_active,
        "model_profiles.test_connection": test_connection,
        "model_profiles.fetch_model_list": fetch_model_list,
    }


_INLINE_PROFILE_FIELDS: frozenset[str] = frozenset(
    {"provider_format", "base_url", "api_key"}
)


def _resolve_profile_for_probe(
    payload: Mapping[str, object],
    *,
    profile_store: ModelProfileStore,
    require_model_id: bool,
    usage: str,
) -> ModelConfig:
    """Resolve the payload into a runnable ``ModelConfig`` for the
    test_connection / fetch_model_list probe handlers.

    Two payload shapes are accepted (architecture § 3.4 G.2):

    1. **Stored profile**: ``{ id: <profile_id> }`` — load the saved
       profile. Profile must exist and carry an API key.
    2. **Inline credentials**: ``{ provider_format, base_url,
       api_key, model_id? }`` — build an ephemeral profile in
       memory, never persisted. Lets the modal validate before the
       user clicks Save.

    Mixing the two (``{ id, base_url }``) is ambiguous and raises
    ``bridge.invalid_argument``.
    """

    has_id = isinstance(payload.get("id"), str) and payload.get("id")
    inline_keys_present = _INLINE_PROFILE_FIELDS & set(payload.keys())
    has_inline = bool(inline_keys_present)

    if has_id and has_inline:
        raise BridgeError.invalid_argument(
            "Pass either { id } OR inline credentials, not both.",
            field="id",
            details={
                "reason": "ambiguous_payload",
                "inline_keys": sorted(inline_keys_present),
            },
        )
    if not has_id and not has_inline:
        raise BridgeError.invalid_argument(
            "Either { id } or { provider_format, base_url, api_key } "
            "is required.",
            field="id",
        )

    if has_id:
        profile_id = expect_string(payload, "id")
        profile = profile_store.get(profile_id)
        if profile is None:
            raise BridgeError.not_found(
                f"profile {profile_id!r} does not exist.",
                details={"profile_id": profile_id},
            )
        if not profile.api_keys:
            raise BridgeError.invalid_argument(
                f"profile has no API key configured; set one before {usage}.",
                field="api_keys",
                details={"reason": "missing_api_key"},
            )
        return profile

    # Inline path: build a transient ModelConfig.
    provider_raw = expect_string(payload, "provider_format")
    base_url = expect_string(payload, "base_url")
    api_key = expect_string(payload, "api_key", allow_empty=False)
    try:
        provider_format = ProviderFormat(provider_raw)
    except ValueError as exc:
        raise BridgeError.invalid_argument(
            f"unsupported provider_format: {provider_raw!r}",
            field="provider_format",
        ) from exc

    model_id_raw = payload.get("model_id")
    if require_model_id:
        if not isinstance(model_id_raw, str) or not model_id_raw.strip():
            raise BridgeError.invalid_argument(
                "model_id is required for inline test_connection.",
                field="model_id",
            )
        model_id = model_id_raw.strip()
    else:
        model_id = (
            model_id_raw.strip()
            if isinstance(model_id_raw, str) and model_id_raw.strip()
            else "probe"
        )

    custom_headers_raw = payload.get("custom_headers")
    custom_headers: tuple[tuple[str, str], ...] = ()
    if isinstance(custom_headers_raw, list):
        custom_headers = tuple(
            (str(item[0]), str(item[1]))
            for item in custom_headers_raw
            if isinstance(item, (list, tuple)) and len(item) == 2
        )

    return ModelConfig(
        id="inline-probe",
        display_name="inline probe",
        provider_format=provider_format,
        base_url=base_url,
        model_id=model_id,
        api_keys=(api_key,),
        custom_headers=custom_headers,
    )


def _status_code_from_error(exc: LlmRequestError) -> int | None:
    """Best-effort: extract HTTP status from the error message.

    LlmRequestError messages contain ``HTTP <code>`` for http_error /
    rotatable failures. Returning ``None`` is acceptable for transport
    errors where no response was received.
    """

    text = str(exc)
    marker = "HTTP "
    if marker in text:
        tail = text.split(marker, 1)[1]
        digits = tail.split(":", 1)[0].split(" ", 1)[0].strip()
        try:
            return int(digits)
        except ValueError:
            return None
    return None


def _do_fetch_models(
    profile: ModelConfig, http_client_factory
) -> list[dict[str, object]]:
    """Hit the provider's ``/models`` endpoint and return ``[{id, display_name?}]``.

    OpenAI/Sakura/Custom: ``GET <base_url>/models`` with bearer auth.
    Google: ``GET <base_url>/v1beta/models?key=<api_key>``.
    Anthropic is rejected upstream — no list endpoint.
    """

    api_key = profile.api_keys[0]
    headers = profile.custom_headers_dict()
    timeout = max(5.0, min(profile.timeout_seconds, 30.0))
    if profile.provider_format is ProviderFormat.GOOGLE:
        url = f"{profile.base_url.rstrip('/')}/v1beta/models?key={api_key}"
        request_headers = {**headers}
    else:
        url = f"{profile.base_url.rstrip('/')}/models"
        request_headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            **headers,
        }
    with http_client_factory(timeout=timeout) as client:
        try:
            response = client.get(url, headers=request_headers)
        except httpx.HTTPError as exc:
            # ``exc`` may include the request URL, which carries the
            # Google API key as a query parameter. Redact before bubbling.
            from transoria.llm.client import _redact_url

            raise LlmRequestError(
                f"transport failed: {_redact_url(str(exc))}",
                code="llm.transport_error",
            ) from exc
    if response.status_code >= 400:
        raise LlmRequestError(
            f"HTTP {response.status_code}: {response.text[:200]}",
            code="llm.http_error",
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise LlmRequestError(
            f"invalid JSON response: {exc}", code="llm.malformed_response"
        ) from exc

    entries: list[dict[str, object]] = []
    if profile.provider_format is ProviderFormat.GOOGLE:
        models = body.get("models") if isinstance(body, dict) else None
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", ""))
                model_id = name.split("/")[-1] if "/" in name else name
                if not model_id:
                    continue
                entries.append(
                    {
                        "id": model_id,
                        "display_name": str(item.get("displayName", model_id)),
                    }
                )
    else:
        items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list) and isinstance(body, list):
            items = body
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    model_id = str(item.get("id", ""))
                    if not model_id:
                        continue
                    entries.append({"id": model_id})
                elif isinstance(item, str):
                    entries.append({"id": item})
    return entries


def _profile_from_payload(body: Mapping[str, object]) -> ModelConfig:
    """Build a fresh ModelConfig for create.

    The id is generated by us if missing so the frontend doesn't have to
    guess; api_keys come in as plain strings on the optional ``api_keys``
    field.
    """

    payload = dict(body)
    payload.setdefault("id", _generate_id(payload))
    api_keys = payload.pop("api_keys", ())
    config = ModelConfig.from_dict(payload)
    if isinstance(api_keys, list):
        config = config.with_api_keys(tuple(str(k) for k in api_keys))
    return config


def _generate_id(body: Mapping[str, object]) -> str:
    seed = str(body.get("display_name") or body.get("model_id") or "profile")
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in seed).strip("-")
    if not slug:
        slug = "profile"
    import secrets  # noqa: PLC0415

    return f"{slug}-{secrets.token_hex(3)}"


def _coerce_patch(patch: Mapping[str, object]) -> dict[str, object]:
    """Coerce string-encoded enum values when the frontend sends them.

    JSON gives us the same shape as ``ModelConfig.from_dict`` expects,
    except enum strings need wrapping back into the dataclass enum types
    so ``replace`` works.
    """

    coerced: dict[str, object] = {}
    for key, value in patch.items():
        if key == "provider_format" and isinstance(value, str):
            coerced[key] = ProviderFormat(value)
        elif key == "thinking_level" and isinstance(value, str):
            coerced[key] = ThinkingLevel(value)
        elif key == "custom_headers" and isinstance(value, list):
            coerced[key] = tuple(
                (str(pair[0]), str(pair[1]))
                for pair in value
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            )
        else:
            coerced[key] = value
    return coerced


def _field_from_exc(exc: Exception) -> str | None:
    message = str(exc)
    if "Unknown profile field" in message and "'" in message:
        tail = message.split("'", 1)[1]
        if "'" in tail:
            return tail.split("'", 1)[0]
    return None


def _app_settings_dict(value: object) -> dict[str, object]:
    from dataclasses import asdict  # noqa: PLC0415

    return asdict(value)  # type: ignore[arg-type]


def _default_chat_transport() -> ChatTransport:
    return HttpxChatTransport()


def _default_http_client_factory(timeout: float):
    return httpx.Client(timeout=timeout)


def _proxy_aware_chat_transport(settings_store: SettingsStore) -> Callable[[], ChatTransport]:
    """Read ``app.proxy_url`` on every transport construction so the
    Test Connection / Fetch Models buttons honor the user's current
    proxy without an app restart."""

    def factory() -> ChatTransport:
        proxy = ""
        try:
            proxy = (settings_store.load_all().app.proxy_url or "").strip()
        except Exception:  # noqa: BLE001
            proxy = ""
        return HttpxChatTransport(proxy=proxy or None)

    return factory


def _proxy_aware_http_client_factory(settings_store: SettingsStore) -> Callable[..., httpx.Client]:
    def factory(timeout: float):
        proxy = ""
        try:
            proxy = (settings_store.load_all().app.proxy_url or "").strip()
        except Exception:  # noqa: BLE001
            proxy = ""
        kwargs: dict[str, object] = {"timeout": timeout}
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.Client(**kwargs)

    return factory


def register(
    router: BridgeRouter,
    *,
    profile_store: ModelProfileStore,
    settings_store: SettingsStore,
    chat_transport_factory: Callable[[], ChatTransport] | None = None,
    http_client_factory: Callable[..., httpx.Client] | None = None,
) -> None:
    handlers = _build_handlers(
        profile_store,
        settings_store,
        chat_transport_factory=(
            chat_transport_factory
            if chat_transport_factory is not None
            else _proxy_aware_chat_transport(settings_store)
        ),
        http_client_factory=(
            http_client_factory
            if http_client_factory is not None
            else _proxy_aware_http_client_factory(settings_store)
        ),
    )
    for method, handler in handlers.items():
        router.register(method, handler)  # type: ignore[arg-type]


__all__ = ["register"]
