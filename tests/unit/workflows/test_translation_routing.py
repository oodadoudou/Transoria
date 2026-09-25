from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Mapping

from transoria.llm import ModelConfig, ProviderFormat
from transoria.llm.client import TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask, SubtaskResult
from transoria.workflows.translation import routing
from transoria.workflows.translation.config import TranslationRouteConfig


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


def _route(profile_id: str, rpm_limit: int) -> TranslationRouteConfig:
    return TranslationRouteConfig(
        model=ModelConfig(
            id=profile_id,
            display_name=profile_id,
            provider_format=ProviderFormat.OPENAI,
            base_url="https://example.test/v1",
            model_id=profile_id,
            api_keys=("key",),
            rpm_limit=rpm_limit,
        ),
        prompt_preset=default_preset(PromptKind.TRANSLATION),
    )


@dataclass
class _BlockingRunner:
    started: list[str] = field(default_factory=list)
    gates: dict[str, asyncio.Future[None]] = field(default_factory=dict)

    async def run(self, subtask: Subtask) -> SubtaskResult:
        self.started.append(subtask.id)
        gate = asyncio.get_running_loop().create_future()
        self.gates[subtask.id] = gate
        await gate
        return SubtaskResult(response_content="ok")


def test_advanced_routes_weight_active_work_by_rpm() -> None:
    async def scenario() -> None:
        main = _BlockingRunner()
        helper = _BlockingRunner()
        runner = routing.RoutedTranslationRunner(
            ((_route("main", 48), main), (_route("helper", 24), helper)),
            group_concurrency=72,
        )
        tasks = {
            str(index): asyncio.create_task(
                runner.run(Subtask(id=str(index), task_id="task"))
            )
            for index in range(72)
        }
        await asyncio.sleep(0)
        assert (len(main.started), len(helper.started)) == (48, 24)

        main_id = main.started[0]
        main.gates[main_id].set_result(None)
        assert (await tasks[main_id]).route_profile_id == "main"
        main_replacement = asyncio.create_task(
            runner.run(Subtask(id="main-next", task_id="task"))
        )
        await asyncio.sleep(0)
        assert main.started[-1] == "main-next"

        helper_id = helper.started[0]
        helper.gates[helper_id].set_result(None)
        assert (await tasks[helper_id]).route_profile_id == "helper"
        helper_replacement = asyncio.create_task(
            runner.run(Subtask(id="helper-next", task_id="task"))
        )
        await asyncio.sleep(0)
        assert helper.started[-1] == "helper-next"

        for gate in (*main.gates.values(), *helper.gates.values()):
            if not gate.done():
                gate.set_result(None)
        await asyncio.gather(*tasks.values(), main_replacement, helper_replacement)

    asyncio.run(scenario())


def test_advanced_routes_keep_rpm_weight_with_one_worker() -> None:
    @dataclass
    class InstantRunner:
        calls: int = 0

        async def run(self, subtask: Subtask) -> SubtaskResult:
            self.calls += 1
            return SubtaskResult(response_content="ok")

    async def scenario() -> tuple[int, int]:
        main = InstantRunner()
        helper = InstantRunner()
        runner = routing.RoutedTranslationRunner(
            ((_route("main", 48), main), (_route("helper", 24), helper)),
            group_concurrency=1,
        )
        for index in range(12):
            await runner.run(Subtask(id=str(index), task_id="task"))
        return main.calls, helper.calls

    assert asyncio.run(scenario()) == (8, 4)


def test_advanced_routes_share_profile_rpm_between_prompt_routes() -> None:
    async def scenario() -> tuple[int, int, int]:
        first = _BlockingRunner()
        second = _BlockingRunner()
        other = _BlockingRunner()
        runner = routing.RoutedTranslationRunner(
            (
                (_route("shared", 48), first),
                (_route("shared", 48), second),
                (_route("other", 24), other),
            ),
            group_concurrency=72,
        )
        tasks = [
            asyncio.create_task(runner.run(Subtask(id=str(i), task_id="task")))
            for i in range(72)
        ]
        await asyncio.sleep(0)
        counts = len(first.started), len(second.started), len(other.started)
        for blocked in (first, second, other):
            for gate in blocked.gates.values():
                gate.set_result(None)
        await asyncio.gather(*tasks)
        return counts

    assert asyncio.run(scenario()) == (24, 24, 24)


def test_advanced_unlimited_route_still_receives_work() -> None:
    async def scenario() -> tuple[int, int]:
        unlimited = _BlockingRunner()
        limited = _BlockingRunner()
        runner = routing.RoutedTranslationRunner(
            ((_route("unlimited", 0), unlimited), (_route("limited", 24), limited)),
            group_concurrency=72,
        )
        tasks = [
            asyncio.create_task(runner.run(Subtask(id=str(i), task_id="task")))
            for i in range(72)
        ]
        await asyncio.sleep(0)
        counts = len(unlimited.started), len(limited.started)
        for blocked in (unlimited, limited):
            for gate in blocked.gates.values():
                gate.set_result(None)
        await asyncio.gather(*tasks)
        return counts

    assert asyncio.run(scenario()) == (54, 18)
