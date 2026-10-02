from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

import httpx
import pytest

from transoria.domain import Language, SubtaskStatus, TaskKind, TaskStatus
from transoria.llm import LlmClient, ModelConfig, ProviderFormat
from transoria.llm.client import HttpxChatTransport, TransportResult
from transoria.prompts import PromptKind, default_preset
from transoria.runtime import Subtask, TaskCache, TaskExecutor, TaskRecord
from transoria.workflows.glossary import (
    GlossaryChunk,
    GlossarySubtaskRunner,
    encode_glossary_payload,
)


@dataclass
class StreamFlagTransport:
    captured: list[bool] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.captured.append(bool(payload.get("stream", False)))
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"src":"신해범","dst":"申海范","type":"Male Name"}',
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return TransportResult(200, body)


def _make_subtask() -> Subtask:
    chunk = GlossaryChunk(
        chunk_id="chunk", source_file=Path("/in/Sample.txt"), text="신해범 walks"
    )
    return Subtask(
        id=chunk.chunk_id, task_id="t", request_payload=encode_glossary_payload(chunk)
    )


def _model() -> ModelConfig:
    return ModelConfig(
        id="m",
        display_name="m",
        provider_format=ProviderFormat.OPENAI,
        base_url="https://example/api/v1/",
        model_id="m",
        api_keys=("k",),
    )


def test_glossary_runner_streams_when_stream_flag_is_true() -> None:
    transport = StreamFlagTransport()
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        stream=True,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.captured == [True]


def test_glossary_runner_does_not_stream_by_default() -> None:
    transport = StreamFlagTransport()
    runner = GlossarySubtaskRunner(
        client=LlmClient(transport=transport),
        model=_model(),
        prompt_preset=default_preset(PromptKind.GLOSSARY),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )

    asyncio.run(runner.run(_make_subtask()))

    assert transport.captured == [False]


@pytest.mark.parametrize("heartbeat", [False, True])
@pytest.mark.parametrize("stop", [False, True])
def test_glossary_partial_stream_is_bounded_and_stop_resume_preserves_completed(
    tmp_path: Path, heartbeat: bool, stop: bool
) -> None:
    streams = []
    started = asyncio.Event()

    class StalledStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"{\\\""}}]}\n\n'
            if len(streams) == 3:
                started.set()
            if heartbeat:
                while True:
                    await asyncio.sleep(0.005)
                    yield b": keepalive\n\n"
            else:
                await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    def handler(request):
        stream = StalledStream()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    cache = TaskCache(root=tmp_path)
    payload = _make_subtask().request_payload
    completed = [
        Subtask(
            id=f"done-{i}", task_id="t", status=SubtaskStatus.COMPLETED,
            response_content="original cached result",
        )
        for i in range(126)
    ]
    pending = [
        Subtask(
            id=f"pending-{i}", task_id="t",
            request_payload={**payload, "chunk_id": f"pending-{i}"},
        )
        for i in range(3)
    ]
    cache.write_seed(
        TaskRecord(
            id="t", kind=TaskKind.GLOSSARY, status=TaskStatus.PENDING,
            created_at="2026-10-03T00:00:00+00:00",
        ),
        [*completed, *pending],
    )

    async def run():
        transport = HttpxChatTransport()
        transport._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        client = LlmClient(transport=transport)
        runner = GlossarySubtaskRunner(
            client=client,
            model=replace(_model(), timeout_seconds=1 if stop else 0.05, rpm_limit=0),
            prompt_preset=default_preset(PromptKind.GLOSSARY),
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
            stream=True, transport_retry_attempts=0,
        )
        executor = TaskExecutor(
            cache=cache, runner=runner, concurrency_limit=3,
            rpm_limit=0, stop_drain_seconds=0.02,
        )
        task = asyncio.create_task(executor.run("t"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            if stop:
                await asyncio.to_thread(executor.request_stop)
            return await asyncio.wait_for(task, 1)
        finally:
            await client.aclose()

    snapshot = asyncio.run(run())
    assert snapshot.record.status is (TaskStatus.STOPPED if stop else TaskStatus.FAILED)
    by_id = {subtask.id: subtask for subtask in snapshot.subtasks}
    assert all(by_id[subtask.id] == subtask for subtask in completed)
    expected_status = SubtaskStatus.PENDING if stop else SubtaskStatus.FAILED
    assert all(by_id[subtask.id].status is expected_status for subtask in pending)
    # Timeouts also attempt one smaller rescue chunk per unfinished subtask.
    request_count = 3 if stop else 6
    assert len(streams) == request_count
    assert all(stream.closed for stream in streams)
    events = cache.load_request_events("t")
    assert sum(event.get("phase") == "first_token" for event in events) == request_count
    terminal_status = "cancelled" if stop else "failed"
    assert sum(event.get("status") == terminal_status for event in events) == request_count
    if stop:
        transport = StreamFlagTransport()
        runner = GlossarySubtaskRunner(
            client=LlmClient(transport=transport),
            model=replace(_model(), rpm_limit=0),
            prompt_preset=default_preset(PromptKind.GLOSSARY),
            source_language=Language.KOREAN,
            target_language=Language.CHINESE_SIMPLIFIED,
            stream=True,
        )
        result = asyncio.run(
            TaskExecutor(cache=cache, runner=runner, rpm_limit=0).run("t")
        )
        assert result.record.status is TaskStatus.COMPLETED
        assert transport.captured == [True, True, True]
        assert all(
            subtask.response_content == "original cached result"
            for subtask in result.subtasks if subtask.id.startswith("done-")
        )
