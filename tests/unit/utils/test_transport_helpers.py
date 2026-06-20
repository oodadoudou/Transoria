"""Smoke tests for the shared test transports."""

from __future__ import annotations

import asyncio

from transoria.llm.client import TransportResult
from tests.helpers.transport import (
    CandidateEmittingTransport,
    EchoTranslationTransport,
    QueuedTransport,
)


def test_queued_transport_returns_pre_built_responses_in_order() -> None:
    transport = QueuedTransport(
        responses=[
            TransportResult(200, {"choices": [{"message": {"content": "first"}}]}),
            TransportResult(200, {"choices": [{"message": {"content": "second"}}]}),
        ]
    )

    first = asyncio.run(
        transport.execute("https://x", {}, {"model": "m", "messages": []}, 1.0)
    )
    second = asyncio.run(
        transport.execute("https://x", {}, {"model": "m", "messages": []}, 1.0)
    )

    assert first.body["choices"][0]["message"]["content"] == "first"
    assert second.body["choices"][0]["message"]["content"] == "second"


def test_echo_translation_transport_marks_each_jsonl_line() -> None:
    transport = EchoTranslationTransport()
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": '[Translate]\n{"0":"hello"}\n{"1":"world"}'}
        ],
    }

    result = asyncio.run(transport.execute("https://x", {}, payload, 1.0))

    content = result.body["choices"][0]["message"]["content"]
    assert "翻译:hello" in content
    assert "翻译:world" in content


def test_candidate_emitting_transport_returns_canned_glossary() -> None:
    transport = CandidateEmittingTransport(
        candidates=(("신해범", "申海范", "Male Name"),)
    )
    payload = {"model": "m", "messages": [{"role": "user", "content": "x"}]}

    result = asyncio.run(transport.execute("https://x", {}, payload, 1.0))

    assert "신해범" in result.body["choices"][0]["message"]["content"]
