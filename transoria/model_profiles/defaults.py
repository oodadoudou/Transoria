"""Seed model profiles.

Frozen at import time. The store seeds these on first run; users may
edit or delete them like any other profile.
"""

from __future__ import annotations

from transoria.llm.config import ModelConfig, ProviderFormat

DEEPSEEK_ID = "preset-deepseek"
ANTHROPIC_ID = "preset-anthropic"
GOOGLE_ID = "preset-google"
OPENAI_ID = "preset-openai"

DEFAULT_PROFILE_IDS: tuple[str, ...] = (
    DEEPSEEK_ID,
    ANTHROPIC_ID,
    GOOGLE_ID,
    OPENAI_ID,
)


def default_profiles() -> tuple[ModelConfig, ...]:
    """Return the seeded profile list.

    Defaults reflect each provider's recommended request/sampling
    parameters for translation-style workloads. Users should adjust
    per-key/concurrency limits to match their plan.
    """

    # ``input_token_limit`` is sized so the derived chunk_size fits
    # comfortably under each preset's ``max_output_tokens`` (chunk × 36
    # tokens/line for output + 1024 thinking budget headroom). Users
    # can raise this in their model config to trade per-line cost for
    # bigger batches; we ship conservative values that work even with
    # thinking enabled at the highest tier.
    return (
        ModelConfig(
            id=DEEPSEEK_ID,
            display_name="DeepSeek",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-chat",
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=60,
            tpm_limit=0,
            retry_attempts=10,
            max_output_tokens=4096,
            input_token_limit=1024,  # → 64-line chunks
            temperature=0.3,
        ),
        ModelConfig(
            id=ANTHROPIC_ID,
            display_name="Anthropic",
            provider_format=ProviderFormat.ANTHROPIC,
            base_url="https://api.anthropic.com",
            model_id="claude-sonnet-4-6",
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=50,
            tpm_limit=0,
            retry_attempts=10,
            max_output_tokens=8192,
            input_token_limit=2048,  # → 128-line chunks
            temperature=1.0,
        ),
        ModelConfig(
            id=GOOGLE_ID,
            display_name="Google",
            provider_format=ProviderFormat.GOOGLE,
            base_url="https://generativelanguage.googleapis.com",
            model_id="gemini-2.5-flash",
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=60,
            tpm_limit=0,
            retry_attempts=10,
            max_output_tokens=8192,
            input_token_limit=2048,  # → 128-line chunks
            temperature=0.7,
            top_p=0.95,
        ),
        ModelConfig(
            id=OPENAI_ID,
            display_name="OpenAI",
            provider_format=ProviderFormat.OPENAI,
            base_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=60,
            tpm_limit=0,
            retry_attempts=10,
            max_output_tokens=4096,
            input_token_limit=1024,  # → 64-line chunks
            temperature=0.3,
        ),
    )


__all__ = [
    "ANTHROPIC_ID",
    "DEEPSEEK_ID",
    "DEFAULT_PROFILE_IDS",
    "GOOGLE_ID",
    "OPENAI_ID",
    "default_profiles",
]
