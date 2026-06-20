from __future__ import annotations

from transoria.llm import TokenUsage


def test_token_usage_total_is_sum_of_input_and_output() -> None:
    usage = TokenUsage(input_tokens=100, output_tokens=42)

    assert usage.total_tokens == 142


def test_token_usage_addition_is_immutable() -> None:
    a = TokenUsage(input_tokens=10, output_tokens=20, cached_input_tokens=2)
    b = TokenUsage(input_tokens=3, output_tokens=4, cached_input_tokens=1)

    combined = a + b

    assert combined == TokenUsage(
        input_tokens=13,
        output_tokens=24,
        cached_input_tokens=3,
    )
    assert a == TokenUsage(input_tokens=10, output_tokens=20, cached_input_tokens=2)
    assert b == TokenUsage(input_tokens=3, output_tokens=4, cached_input_tokens=1)


def test_token_usage_addition_preserves_estimated_flag() -> None:
    combined = TokenUsage(input_tokens=1) + TokenUsage(
        output_tokens=2,
        estimated=True,
    )

    assert combined == TokenUsage(
        input_tokens=1,
        output_tokens=2,
        estimated=True,
    )


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


def test_token_usage_from_openai_usage_reads_standard_cached_tokens() -> None:
    usage = TokenUsage.from_openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 80},
        }
    )

    assert usage == TokenUsage(
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=80,
    )


def test_token_usage_from_openai_usage_reads_deepseek_cache_hits() -> None:
    usage = TokenUsage.from_openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 70,
            "prompt_cache_miss_tokens": 30,
        }
    )

    assert usage == TokenUsage(
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=70,
    )


def test_token_usage_from_openai_usage_reconstructs_deepseek_prompt_total() -> None:
    usage = TokenUsage.from_openai_usage(
        {
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 70,
            "prompt_cache_miss_tokens": 30,
        }
    )

    assert usage == TokenUsage(
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=70,
    )


def test_token_usage_from_openai_usage_returns_zero_when_missing() -> None:
    assert TokenUsage.from_openai_usage(None) == TokenUsage()
    assert TokenUsage.from_openai_usage({}) == TokenUsage()


def test_token_usage_from_openai_usage_reads_estimated_marker() -> None:
    usage = TokenUsage.from_openai_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "transoria_estimated": True,
        }
    )

    assert usage == TokenUsage(input_tokens=10, output_tokens=20, estimated=True)
