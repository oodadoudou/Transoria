from __future__ import annotations

from transoria.llm import TokenUsage


def test_token_usage_total_is_sum_of_input_and_output() -> None:
    usage = TokenUsage(input_tokens=100, output_tokens=42)

    assert usage.total_tokens == 142


def test_token_usage_addition_is_immutable() -> None:
    a = TokenUsage(input_tokens=10, output_tokens=20)
    b = TokenUsage(input_tokens=3, output_tokens=4)

    combined = a + b

    assert combined == TokenUsage(input_tokens=13, output_tokens=24)
    assert a == TokenUsage(input_tokens=10, output_tokens=20)
    assert b == TokenUsage(input_tokens=3, output_tokens=4)


def test_token_usage_from_openai_usage_handles_legacy_field_names() -> None:
    usage = TokenUsage.from_openai_usage(
        {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    )

    assert usage == TokenUsage(input_tokens=10, output_tokens=20)


def test_token_usage_from_openai_usage_handles_new_field_names() -> None:
    usage = TokenUsage.from_openai_usage(
        {"input_tokens": 5, "output_tokens": 7}
    )

    assert usage == TokenUsage(input_tokens=5, output_tokens=7)


def test_token_usage_from_openai_usage_returns_zero_when_missing() -> None:
    assert TokenUsage.from_openai_usage(None) == TokenUsage()
    assert TokenUsage.from_openai_usage({}) == TokenUsage()
