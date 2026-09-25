from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Mapping

from transoria.llm.client import TransportResult
from transoria.workflows.translation import routing


@dataclass
class _Transport:
    calls: list[str] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.calls.append(url)
        return TransportResult(200, {})


def test_advanced_transport_acquires_for_each_http_attempt(monkeypatch) -> None:
    limits: dict[str, list[int]] = {}

    class _Limiter:
        def __init__(self, profile_id: str) -> None:
            self.profile_id = profile_id

        async def acquire(self, limit: int) -> None:
            limits.setdefault(self.profile_id, []).append(limit)

    monkeypatch.setattr(routing, "shared_rpm_limiter", lambda profile_id: _Limiter(profile_id))
    transport = _Transport()
    first = routing.RouteRateLimitedTransport(transport, "provider-a", 30)
    second = routing.RouteRateLimitedTransport(transport, "provider-b", 12)

    async def scenario() -> None:
        await first.execute("a", {}, {}, 1)
        await first.execute_observed("a", {}, {}, 1, None)
        await second.execute("b", {}, {}, 1)

    asyncio.run(scenario())
    assert limits == {"provider-a": [30, 30], "provider-b": [12]}
    assert transport.calls == ["a", "a", "b"]
