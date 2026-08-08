"""LLM client layer: provider config, request/response decoding, token usage."""

from transoria.llm.client import (
    ChatRequest,
    ChatResponse,
    ChatTransport,
    HttpxChatTransport,
    LlmClient,
    LlmDegenerateOutputError,
    LlmRequestError,
    LlmTruncatedResponseError,
    NoApiKeyError,
)
from transoria.llm.config import ModelConfig, ProviderFormat, ThinkingLevel
from transoria.llm.decoders import (
    GlossaryEntry,
    TranslationLine,
    decode_glossary_jsonl,
    decode_translation_jsonl,
)
from transoria.llm.providers import (
    VOLCENGINE_ARK_BASE_URL,
    VOLCENGINE_ARK_DEFAULT_MODEL_ID,
    resolve_api_keys,
    volcengine_ark_default,
)
from transoria.llm.usage import TokenUsage

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ChatTransport",
    "HttpxChatTransport",
    "LlmClient",
    "LlmDegenerateOutputError",
    "LlmRequestError",
    "LlmTruncatedResponseError",
    "NoApiKeyError",
    "ModelConfig",
    "ProviderFormat",
    "ThinkingLevel",
    "GlossaryEntry",
    "TranslationLine",
    "decode_glossary_jsonl",
    "decode_translation_jsonl",
    "TokenUsage",
    "VOLCENGINE_ARK_BASE_URL",
    "VOLCENGINE_ARK_DEFAULT_MODEL_ID",
    "resolve_api_keys",
    "volcengine_ark_default",
]
