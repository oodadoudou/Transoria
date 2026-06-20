from __future__ import annotations

from transoria.workflows.executor_pacing import llm_launch_spacing_seconds


def test_llm_launch_spacing_only_applies_to_high_concurrency() -> None:
    assert llm_launch_spacing_seconds(1) == 0.0
    assert llm_launch_spacing_seconds(7) == 0.0
    assert llm_launch_spacing_seconds(8) == 0.05
