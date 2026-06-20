"""Pytest fixtures + global setup for the Transoria suite."""

from __future__ import annotations

import os

import pytest


# Suppress LLM IO logs during tests — the runner prints SEND/RECV lines
# to stderr by default (see ``transoria/llm/io_log.py``); under pytest
# this would flood the captured output and slow runs.
os.environ.setdefault("TRANSORIA_LLM_LOG", "off")


@pytest.fixture(autouse=True)
def _zero_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transport-retry backoff is fixed internal policy (1s→30s); zero it
    in tests so retry-exercising cases finish in milliseconds. Tests that
    assert the backoff curve pass explicit ``*_backoff_seconds`` to
    ``retry_async`` and are unaffected by this default override."""

    monkeypatch.setattr(
        "transoria.llm.retry._DEFAULT_INITIAL_BACKOFF_SECONDS", 0.0
    )
    monkeypatch.setattr("transoria.llm.retry._DEFAULT_MAX_BACKOFF_SECONDS", 0.0)
