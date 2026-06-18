"""Token usage accounting for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping


# Public type alias for any "count tokens in a string" callable. The
# glossary chunker and (later) the TPM limiter accept one of these. Real
# implementations come from `tiktoken`, the Anthropic SDK's counter, or a
# provider-specific tokenizer; for tests a lambda is fine.
TokenCounter = Callable[[str], int]


# Conservative default chars-per-token ratio used when no real tokenizer is
# configured. CJK-heavy text averages ~2 chars/token; ASCII averages ~4. The
# midpoint over-estimates ASCII and under-estimates pure CJK, but is good
# enough as a TPM-budget proxy until a tokenizer plugin is added.
_DEFAULT_CHARS_PER_TOKEN = 3.5


def estimate_tokens_from_text(text: str, *, chars_per_token: float = _DEFAULT_CHARS_PER_TOKEN) -> int:
    """Char-count → token estimate. Always returns at least 1 for non-empty input."""

    if not text:
        return 0
    return max(1, int(len(text) / max(0.1, chars_per_token)))


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=(
                self.cached_input_tokens + other.cached_input_tokens
            ),
            estimated=self.estimated or other.estimated,
        )

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "total_tokens": self.total_tokens,
            "usage_estimated": self.estimated,
        }

    @classmethod
    def from_openai_usage(cls, usage: Mapping[str, object] | None) -> "TokenUsage":
        """Parse an OpenAI-compatible ``usage`` block.

        Accepts ``prompt_tokens`` / ``completion_tokens`` (OpenAI legacy and
        most compatibles including Volcengine Ark) and falls back to
        ``input_tokens`` / ``output_tokens`` when the provider uses the newer
        Anthropic-style names. Missing values default to 0.
        """

        if not usage:
            return cls()
        cached_tokens = 0
        details = usage.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            cached_tokens = int(details.get("cached_tokens") or 0)
        if cached_tokens <= 0:
            cached_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        if prompt_tokens is None and (
            "prompt_cache_hit_tokens" in usage
            or "prompt_cache_miss_tokens" in usage
        ):
            prompt_tokens = int(usage.get("prompt_cache_hit_tokens") or 0) + int(
                usage.get("prompt_cache_miss_tokens") or 0
            )
        return cls(
            input_tokens=int(prompt_tokens or 0),
            output_tokens=int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            ),
            cached_input_tokens=cached_tokens,
            estimated=bool(
                usage.get("usage_estimated")
                or usage.get("transoria_estimated")
                or usage.get("estimated")
            ),
        )


__all__ = ["TokenCounter", "TokenUsage", "estimate_tokens_from_text"]
