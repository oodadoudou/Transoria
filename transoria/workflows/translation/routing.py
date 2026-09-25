"""Optional multi-route translation runner for advanced workflow presets."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Mapping

from transoria.llm.client import ChatRequest, ChatResponse, ChatTransport, LlmClient, TransportResult
from transoria.runtime.executor import SubtaskFailedWithResult, SubtaskResult, SubtaskRunner
from transoria.runtime.rate_limit import shared_rpm_limiter
from transoria.runtime.subtask import Subtask
from transoria.workflows.translation.config import TranslationRouteConfig


def route_snapshot(route: TranslationRouteConfig) -> dict[str, object]:
    return {
        "model": {**route.model.to_dict(), "api_keys": []},
        "prompt_preset": route.prompt_preset.to_dict(),
    }


@dataclass
class RouteRateLimitedTransport:
    transport: ChatTransport
    profile_id: str
    rpm_limit: int

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        await shared_rpm_limiter(self.profile_id).acquire(self.rpm_limit)
        return await asyncio.wait_for(
            self.transport.execute(url, headers, payload, timeout), timeout=timeout
        )

    async def execute_observed(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
        request_log: object,
        detect_stream_repetition: bool = False,
    ) -> TransportResult:
        await shared_rpm_limiter(self.profile_id).acquire(self.rpm_limit)
        observed = getattr(self.transport, "execute_observed", None)
        if callable(observed):
            return await asyncio.wait_for(
                observed(
                    url, headers, payload, timeout, request_log, detect_stream_repetition
                ),
                timeout=timeout,
            )
        return await asyncio.wait_for(
            self.transport.execute(url, headers, payload, timeout), timeout=timeout
        )


@dataclass
class RouteLimitedClient:
    client: LlmClient
    _clients: dict[str, LlmClient] = field(default_factory=dict, init=False, repr=False)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        route_client = self._clients.get(request.model.id)
        if route_client is None:
            route_client = LlmClient(
                transport=RouteRateLimitedTransport(
                    self.client.transport, request.model.id, request.model.rpm_limit
                )
            )
            self._clients[request.model.id] = route_client
        return await route_client.chat(request)

    async def aclose(self) -> None:
        await self.client.aclose()


@dataclass
class RoutedTranslationRunner:
    routes: tuple[tuple[TranslationRouteConfig, SubtaskRunner], ...]
    group_concurrency: int
    _active: list[int] = field(default_factory=list, init=False, repr=False)
    _assigned: list[int] = field(default_factory=list, init=False, repr=False)
    _weights: tuple[float, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.routes:
            raise ValueError("Routed translation requires at least one route.")
        profile_counts = Counter(route.model.id for route, _ in self.routes)
        profile_rpm: dict[str, int] = {}
        for route, _ in self.routes:
            profile_id = route.model.id
            profile_rpm[profile_id] = max(
                profile_rpm.get(profile_id, 0), route.model.rpm_limit
            )
        finite_rpm = sum(max(0, rpm) for rpm in profile_rpm.values())
        unlimited_weight = max(1, self.group_concurrency, finite_rpm)
        self._weights = tuple(
            (max(0, route.model.rpm_limit) or unlimited_weight)
            / profile_counts[route.model.id]
            for route, _ in self.routes
        )
        self._active = [0] * len(self.routes)
        self._assigned = [0] * len(self.routes)

    async def run(self, subtask: Subtask) -> SubtaskResult:
        index = min(
            range(len(self.routes)),
            key=lambda i: (
                self._active[i] / self._weights[i],
                self._assigned[i] / self._weights[i],
                i,
            ),
        )
        self._active[index] += 1
        self._assigned[index] += 1
        route, runner = self.routes[index]
        try:
            result = await runner.run(subtask)
        except SubtaskFailedWithResult as exc:
            raise SubtaskFailedWithResult(
                str(exc),
                result=replace(exc.result, route_profile_id=route.model.id),
                code=exc.code,
            ) from exc
        else:
            return replace(result, route_profile_id=route.model.id)
        finally:
            self._active[index] -= 1


__all__ = ["RouteLimitedClient", "RoutedTranslationRunner", "route_snapshot"]
