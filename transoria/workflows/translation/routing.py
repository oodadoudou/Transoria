"""Optional multi-route translation runner for advanced workflow presets."""

from __future__ import annotations

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
        return await self.transport.execute(url, headers, payload, timeout)

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
            return await observed(
                url, headers, payload, timeout, request_log, detect_stream_repetition
            )
        return await self.transport.execute(url, headers, payload, timeout)


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
    _next_index: int = field(default=0, init=False, repr=False)

    async def run(self, subtask: Subtask) -> SubtaskResult:
        index = self._next_index % len(self.routes)
        self._next_index += 1
        route, runner = self.routes[index]
        try:
            result = await runner.run(subtask)
        except SubtaskFailedWithResult as exc:
            raise SubtaskFailedWithResult(
                str(exc),
                result=replace(exc.result, route_profile_id=route.model.id),
                code=exc.code,
            ) from exc
        return replace(result, route_profile_id=route.model.id)


__all__ = ["RouteLimitedClient", "RoutedTranslationRunner", "route_snapshot"]
