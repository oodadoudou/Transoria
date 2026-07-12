"""Shared test transports for the LLM client.

Tests previously defined an ad-hoc ``FakeTransport`` per file, which made
maintenance noisy when the transport contract evolved. This module is the
canonical place for the patterns most tests need:

- :class:`QueuedTransport` — fixed list of pre-built ``TransportResult``
  responses; raises if the queue is exhausted.
- :class:`EchoTranslationTransport` — reads JSONL from the user prompt's
  ``[Translate]`` section, echoes each line back with a configurable
  prefix; useful for orchestrator end-to-end tests where exact responses
  don't matter as long as every input line gets a translated output.
- :class:`CandidateEmittingTransport` — returns a fixed roster of glossary
  candidates per request; mirrors the glossary orchestrator's "happy path"
  test transport.

Existing tests are not retrofitted to use these helpers (low ROI for the
churn). New tests should prefer the helpers when their patterns fit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from transoria.llm.client import TransportResult


@dataclass
class QueuedTransport:
    responses: list[TransportResult] = field(default_factory=list)
    captured: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        self.captured.append(
            {"url": url, "headers": dict(headers), "payload": dict(payload), "timeout": timeout}
        )
        if not self.responses:
            raise AssertionError(
                "QueuedTransport ran out of responses — caller queued too few"
            )
        return self.responses.pop(0)


@dataclass
class EchoTranslationTransport:
    prefix: str = "翻译:"
    include_source: bool = True
    forced_failure_calls: tuple[int, ...] = ()
    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        index = len(self.requests)
        self.requests.append(dict(payload))
        if index in self.forced_failure_calls:
            return TransportResult(500, {"error": "boom"})
        user_message = payload["messages"][-1]["content"]
        translate_section = user_message.rsplit("[Translate]\n", 1)[-1]
        lines: list[str] = []
        for line in translate_section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Tolerate non-JSON lines (e.g. trailing contract reminders
            # appended after the JSONL body) so the transport stays usable
            # as the runner's prompt structure evolves.
            if not stripped.startswith("{"):
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            for key, value in parsed.items():
                translated = (
                    f"{self.prefix}{value}"
                    if self.include_source
                    else f"{self.prefix}{key}"
                )
                lines.append(json.dumps({key: translated}, ensure_ascii=False))
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        return TransportResult(200, body)


@dataclass
class CandidateEmittingTransport:
    candidates: Sequence[tuple[str, str, str]] = (
        ("신해범", "申海范", "Male Name"),
    )
    forced_failure_calls: tuple[int, ...] = ()
    requests: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResult:
        index = len(self.requests)
        self.requests.append(dict(payload))
        if index in self.forced_failure_calls:
            return TransportResult(500, {"error": "boom"})
        lines = [
            json.dumps({"src": s, "dst": d, "type": i}, ensure_ascii=False)
            for s, d, i in self.candidates
        ]
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "\n".join(lines)}}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 13},
        }
        return TransportResult(200, body)


__all__ = [
    "CandidateEmittingTransport",
    "EchoTranslationTransport",
    "QueuedTransport",
]
