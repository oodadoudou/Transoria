"""Tests for proxy plumbing in ``HttpxChatTransport``.

We don't actually open a TCP connection — patching ``httpx.AsyncClient``
and asserting on the kwargs is enough to verify that ``app.proxy_url``
is threaded through to the network layer.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx

from transoria.llm.client import HttpxChatTransport


def test_transport_omits_proxy_when_unset() -> None:
    transport = HttpxChatTransport()

    captured: dict[str, object] = {}
    captured_post: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **kwargs: object) -> object:
            captured_post.update(kwargs)

            class FakeResponse:
                status_code = 200

                def json(self) -> dict[str, object]:
                    return {"ok": True}

                @property
                def text(self) -> str:
                    return ""

            return FakeResponse()

    with patch.object(httpx, "AsyncClient", FakeClient):
        asyncio.run(
            transport.execute(
                "https://example.com",
                {"X": "1"},
                {"messages": []},
                timeout=5.0,
            )
        )

    assert "proxy" not in captured
    assert "timeout" not in captured
    assert captured_post["timeout"] == 5.0


def test_transport_passes_proxy_when_configured() -> None:
    transport = HttpxChatTransport(proxy="http://corp-proxy:8080")

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> object:
            class FakeResponse:
                status_code = 200

                def json(self) -> dict[str, object]:
                    return {"ok": True}

                @property
                def text(self) -> str:
                    return ""

            return FakeResponse()

    with patch.object(httpx, "AsyncClient", FakeClient):
        asyncio.run(
            transport.execute(
                "https://example.com",
                {"X": "1"},
                {"messages": []},
                timeout=5.0,
            )
        )

    assert captured["proxy"] == "http://corp-proxy:8080"


def test_transport_treats_empty_string_as_no_proxy() -> None:
    """Settings default ``proxy_url=""`` must not produce an
    ``httpx.AsyncClient(proxy="")`` call (httpx would treat that as a
    literal empty proxy and 4xx)."""

    transport = HttpxChatTransport(proxy="")

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> object:
            class FakeResponse:
                status_code = 200

                def json(self) -> dict[str, object]:
                    return {"ok": True}

                @property
                def text(self) -> str:
                    return ""

            return FakeResponse()

    with patch.object(httpx, "AsyncClient", FakeClient):
        asyncio.run(
            transport.execute(
                "https://example.com",
                {"X": "1"},
                {"messages": []},
                timeout=5.0,
            )
        )

    assert "proxy" not in captured


def test_transport_reuses_async_client_between_requests() -> None:
    transport = HttpxChatTransport()
    constructed = 0
    post_calls = 0

    class FakeClient:
        is_closed = False

        def __init__(self, **_: object) -> None:
            nonlocal constructed
            constructed += 1

        async def post(self, *_: object, **__: object) -> object:
            nonlocal post_calls
            post_calls += 1

            class FakeResponse:
                status_code = 200

                def json(self) -> dict[str, object]:
                    return {"ok": True}

                @property
                def text(self) -> str:
                    return ""

            return FakeResponse()

    async def run() -> None:
        await transport.execute("https://example.com", {}, {}, timeout=5.0)
        await transport.execute("https://example.com", {}, {}, timeout=5.0)

    with patch.object(httpx, "AsyncClient", FakeClient):
        asyncio.run(run())

    assert constructed == 1
    assert post_calls == 2
