"""Optional multi-route translation runner for advanced workflow presets."""

from __future__ import annotations

import asyncio
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Mapping

from transoria.llm.client import ChatRequest, ChatResponse, ChatTransport, LlmClient, TransportResult
from transoria.runtime.executor import SubtaskFailedWithResult, SubtaskResult, SubtaskRunner
from transoria.runtime.rate_limit import SharedRpmLimiter, shared_rpm_limiter
from transoria.runtime.subtask import Subtask
from transoria.workflows.translation.config import TranslationRouteConfig


def route_snapshot(route: TranslationRouteConfig) -> dict[str, object]:
    return {
        "model": {**route.model.to_dict(), "api_keys": []},
        "prompt_preset": route.prompt_preset.to_dict(),
    }


@dataclass
class _RouteAdmission:
    profile_id: str
    limiter: SharedRpmLimiter
    ticket: object
    used: bool = False


_route_admission: ContextVar[_RouteAdmission | None] = ContextVar(
    "route_admission", default=None
)


@dataclass
class RouteRateLimitedTransport:
    transport: ChatTransport
    profile_id: str
    rpm_limit: int

    async def _admit(self) -> None:
        admission = _route_admission.get()
        if (
            admission is not None
            and admission.profile_id == self.profile_id
            and not admission.used
        ):
            admission.used = True
            if admission.limiter.claim(admission.ticket):
                return
        await shared_rpm_limiter(self.profile_id).acquire(self.rpm_limit)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        await self._admit()
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
        await self._admit()
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
        while True:
            order = sorted(
                range(len(self.routes)),
                key=lambda i: (
                    self._active[i] / self._weights[i],
                    self._assigned[i] / self._weights[i],
                    i,
                ),
            )
            for candidate in order:
                route = self.routes[candidate][0]
                limiter = shared_rpm_limiter(route.model.id)
                ticket = limiter.try_reserve(route.model.rpm_limit)
                if ticket is not None:
                    index = candidate
                    admission = _RouteAdmission(route.model.id, limiter, ticket)
                    break
            else:
                delay = min(
                    shared_rpm_limiter(self.routes[i][0].model.id).available_after(
                        self.routes[i][0].model.rpm_limit
                    )
                    for i in order
                )
                await asyncio.sleep(min(max(delay, 0.01), 1.0))
                continue
            break

        self._active[index] += 1
        self._assigned[index] += 1
        route, runner = self.routes[index]
        context_token = _route_admission.set(admission)
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
            _route_admission.reset(context_token)
            if not admission.used:
                admission.limiter.release(admission.ticket)
            self._active[index] -= 1


__all__ = ["RouteLimitedClient", "RoutedTranslationRunner", "route_snapshot"]
