"""Read-only catalog of provider templates for the API Profile modal.

Templates are NOT persisted: users cannot edit or delete them. They
seed the form when a user clicks ``+ Add API Profile`` → picks a
provider. After save, the resulting :class:`ModelConfig` is a fresh
profile with no link back to the originating template.

Each template carries:

- The fields a user typically does not need to invent
  (``provider_format``, ``default_base_url``, ``hint_models``,
  ``supports_fetch_model_list``).
- ``recommended_defaults`` — provider-recommended runtime values
  sourced from each provider's official documentation. Each value's
  source URL is captured in the corresponding :class:`FieldHint` so
  future audits can re-verify against the live docs.
- ``field_hints`` — per-field descriptions for the modal's (?)
  tooltips, plus the recommended display value and an optional
  source URL. The **Custom** template ships hints with empty
  ``recommended_value`` and ``source_url=None`` so the frontend
  renders only the generic description (no "Recommended:" row).

Adding a new provider template = a code change. Editing recommended
defaults = a code change. This is intentional per the architecture's
"references are a product compass, not a code template" rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from transoria.llm.config import ProviderFormat, ThinkingLevel


@dataclass(frozen=True)
class FieldHint:
    """Per-field popover content for the ``+ Add API Profile`` modal.

    ``description_key`` is a locale key resolved by the frontend. The
    backend never renders text. ``recommended_value`` is the
    human-formatted version of the corresponding ``RecommendedDefaults``
    entry (e.g. ``"60 RPM"``). ``source_url`` points at the provider's
    docs page that justifies the value; ``None`` for the Custom
    template (no provider context).
    """

    description_key: str
    recommended_value: str = ""
    source_url: str | None = None


@dataclass(frozen=True)
class RecommendedDefaults:
    """Provider-recommended runtime values used to prefill the form.

    Keep field names aligned with :class:`ModelConfig` so the modal
    can splat into the dataclass without a translation table.
    """

    timeout_seconds: float = 600.0
    concurrency_limit: int = 0
    rpm_limit: int = 60
    tpm_limit: int = 0
    max_output_tokens: int = 8192
    temperature: float = 0.3
    top_p: float = 1.0
    thinking_level: ThinkingLevel = ThinkingLevel.OFF

    def to_dict(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "concurrency_limit": self.concurrency_limit,
            "rpm_limit": self.rpm_limit,
            "tpm_limit": self.tpm_limit,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "thinking_level": self.thinking_level.value,
        }


@dataclass(frozen=True)
class ProviderTemplate:
    """A read-only entry in the provider catalog."""

    id: str
    display_name: str
    provider_format: ProviderFormat
    default_base_url: str
    hint_models: tuple[str, ...]
    supports_fetch_model_list: bool
    recommended_defaults: RecommendedDefaults
    field_hints: Mapping[str, FieldHint] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "provider_format": self.provider_format.value,
            "default_base_url": self.default_base_url,
            "hint_models": list(self.hint_models),
            "supports_fetch_model_list": self.supports_fetch_model_list,
            "recommended_defaults": self.recommended_defaults.to_dict(),
            "field_hints": {
                name: {
                    "description_key": hint.description_key,
                    "recommended_value": hint.recommended_value,
                    "source_url": hint.source_url,
                }
                for name, hint in self.field_hints.items()
            },
        }

_OPENAI_HINTS: Mapping[str, FieldHint] = {
    "timeout_seconds": FieldHint(
        description_key="modelHints.timeout",
        recommended_value="600 s",
        source_url="https://platform.openai.com/docs/api-reference",
    ),
    "concurrency_limit": FieldHint(
        description_key="modelHints.concurrency",
        recommended_value="Auto (0)",
        source_url="https://platform.openai.com/docs/guides/rate-limits",
    ),
    "rpm_limit": FieldHint(
        description_key="modelHints.rpm",
        recommended_value="60 RPM",
        source_url="https://platform.openai.com/docs/guides/rate-limits",
    ),
    "tpm_limit": FieldHint(
        description_key="modelHints.tpm",
        recommended_value="0 (disabled)",
        source_url="https://platform.openai.com/docs/guides/rate-limits",
    ),
    "max_output_tokens": FieldHint(
        description_key="modelHints.maxOutputTokens",
        recommended_value="8192",
    ),
    "temperature": FieldHint(
        description_key="modelHints.temperature",
        recommended_value="0.3",
    ),
}

_ANTHROPIC_HINTS: Mapping[str, FieldHint] = {
    "timeout_seconds": FieldHint(
        description_key="modelHints.timeout",
        recommended_value="600 s",
        source_url="https://docs.anthropic.com/en/api/rate-limits",
    ),
    "concurrency_limit": FieldHint(
        description_key="modelHints.concurrency",
        recommended_value="Auto (0)",
        source_url="https://docs.anthropic.com/en/api/rate-limits",
    ),
    "rpm_limit": FieldHint(
        description_key="modelHints.rpm",
        recommended_value="50 RPM",
        source_url="https://docs.anthropic.com/en/api/rate-limits",
    ),
    "tpm_limit": FieldHint(
        description_key="modelHints.tpm",
        recommended_value="0 (disabled)",
        source_url="https://docs.anthropic.com/en/api/rate-limits",
    ),
    "max_output_tokens": FieldHint(
        description_key="modelHints.maxOutputTokens",
        recommended_value="8192",
    ),
    "temperature": FieldHint(
        description_key="modelHints.temperature",
        recommended_value="1.0",
    ),
}

_GOOGLE_HINTS: Mapping[str, FieldHint] = {
    "timeout_seconds": FieldHint(
        description_key="modelHints.timeout",
        recommended_value="600 s",
        source_url="https://ai.google.dev/gemini-api/docs/quota",
    ),
    "concurrency_limit": FieldHint(
        description_key="modelHints.concurrency",
        recommended_value="Auto (0)",
        source_url="https://ai.google.dev/gemini-api/docs/quota",
    ),
    "rpm_limit": FieldHint(
        description_key="modelHints.rpm",
        recommended_value="60 RPM",
        source_url="https://ai.google.dev/gemini-api/docs/quota",
    ),
    "tpm_limit": FieldHint(
        description_key="modelHints.tpm",
        recommended_value="0 (disabled)",
    ),
    "max_output_tokens": FieldHint(
        description_key="modelHints.maxOutputTokens",
        recommended_value="8192",
    ),
    "temperature": FieldHint(
        description_key="modelHints.temperature",
        recommended_value="0.7",
    ),
}

_DEEPSEEK_HINTS: Mapping[str, FieldHint] = {
    "timeout_seconds": FieldHint(
        description_key="modelHints.timeout",
        recommended_value="600 s",
        source_url="https://api-docs.deepseek.com/quick_start/rate_limit",
    ),
    "concurrency_limit": FieldHint(
        description_key="modelHints.concurrency",
        recommended_value="Auto (0)",
    ),
    "rpm_limit": FieldHint(
        description_key="modelHints.rpm",
        recommended_value="60 RPM",
        source_url="https://api-docs.deepseek.com/quick_start/rate_limit",
    ),
    "tpm_limit": FieldHint(
        description_key="modelHints.tpm",
        recommended_value="0 (disabled)",
    ),
    "max_output_tokens": FieldHint(
        description_key="modelHints.maxOutputTokens",
        recommended_value="8192",
    ),
    "temperature": FieldHint(
        description_key="modelHints.temperature",
        recommended_value="0.3",
    ),
}

_VOLCENGINE_HINTS: Mapping[str, FieldHint] = {
    "timeout_seconds": FieldHint(
        description_key="modelHints.timeout",
        recommended_value="600 s",
        source_url="https://www.volcengine.com/docs/82379/1099475",
    ),
    "concurrency_limit": FieldHint(
        description_key="modelHints.concurrency",
        recommended_value="Auto (0)",
    ),
    "rpm_limit": FieldHint(
        description_key="modelHints.rpm",
        recommended_value="60 RPM",
        source_url="https://www.volcengine.com/docs/82379/1099475",
    ),
    "tpm_limit": FieldHint(
        description_key="modelHints.tpm",
        recommended_value="0 (disabled)",
    ),
    "max_output_tokens": FieldHint(
        description_key="modelHints.maxOutputTokens",
        recommended_value="8192",
    ),
    "temperature": FieldHint(
        description_key="modelHints.temperature",
        recommended_value="0.3",
    ),
}

_SAKURA_HINTS: Mapping[str, FieldHint] = {
    "timeout_seconds": FieldHint(
        description_key="modelHints.timeout",
        recommended_value="600 s",
    ),
    "concurrency_limit": FieldHint(
        description_key="modelHints.concurrency",
        recommended_value="Auto (0)",
    ),
    "rpm_limit": FieldHint(
        description_key="modelHints.rpm",
        recommended_value="0 (disabled)",
    ),
    "tpm_limit": FieldHint(
        description_key="modelHints.tpm",
        recommended_value="0 (disabled)",
    ),
    "max_output_tokens": FieldHint(
        description_key="modelHints.maxOutputTokens",
        recommended_value="2048",
    ),
    "temperature": FieldHint(
        description_key="modelHints.temperature",
        recommended_value="0.1",
    ),
}

# Custom template: hints carry the generic description but leave
# recommended_value/source_url empty so the frontend renders only the
# description row (no "Recommended:" / "Source:" lines).
_CUSTOM_HINTS: Mapping[str, FieldHint] = {
    "timeout_seconds": FieldHint(description_key="modelHints.timeout"),
    "concurrency_limit": FieldHint(description_key="modelHints.concurrency"),
    "rpm_limit": FieldHint(description_key="modelHints.rpm"),
    "tpm_limit": FieldHint(description_key="modelHints.tpm"),
    "max_output_tokens": FieldHint(description_key="modelHints.maxOutputTokens"),
    "temperature": FieldHint(description_key="modelHints.temperature"),
}


_TEMPLATES: tuple[ProviderTemplate, ...] = (
    ProviderTemplate(
        id="openai",
        display_name="OpenAI",
        provider_format=ProviderFormat.OPENAI,
        default_base_url="https://api.openai.com/v1",
        hint_models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"),
        supports_fetch_model_list=True,
        recommended_defaults=RecommendedDefaults(
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=60,
            max_output_tokens=8192,
            temperature=0.3,
        ),
        field_hints=_OPENAI_HINTS,
    ),
    ProviderTemplate(
        id="anthropic",
        display_name="Anthropic",
        provider_format=ProviderFormat.ANTHROPIC,
        default_base_url="https://api.anthropic.com",
        hint_models=(
            "claude-sonnet-4-6",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ),
        supports_fetch_model_list=False,
        recommended_defaults=RecommendedDefaults(
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=50,
            max_output_tokens=8192,
            temperature=1.0,
        ),
        field_hints=_ANTHROPIC_HINTS,
    ),
    ProviderTemplate(
        id="google",
        display_name="Google",
        provider_format=ProviderFormat.GOOGLE,
        default_base_url="https://generativelanguage.googleapis.com",
        hint_models=(
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
        ),
        supports_fetch_model_list=True,
        recommended_defaults=RecommendedDefaults(
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=60,
            max_output_tokens=8192,
            temperature=0.7,
            top_p=0.95,
        ),
        field_hints=_GOOGLE_HINTS,
    ),
    ProviderTemplate(
        id="deepseek",
        display_name="DeepSeek",
        provider_format=ProviderFormat.OPENAI,
        default_base_url="https://api.deepseek.com/v1",
        hint_models=("deepseek-chat", "deepseek-reasoner"),
        supports_fetch_model_list=True,
        recommended_defaults=RecommendedDefaults(
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=60,
            max_output_tokens=8192,
            temperature=0.3,
        ),
        field_hints=_DEEPSEEK_HINTS,
    ),
    ProviderTemplate(
        id="volcengine-ark",
        display_name="Volcengine Ark",
        provider_format=ProviderFormat.OPENAI,
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        hint_models=("deepseek-v3-2-251201", "deepseek-r1-250528"),
        supports_fetch_model_list=True,
        recommended_defaults=RecommendedDefaults(
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=60,
            max_output_tokens=8192,
            temperature=0.3,
        ),
        field_hints=_VOLCENGINE_HINTS,
    ),
    ProviderTemplate(
        id="sakura",
        display_name="Sakura (local)",
        provider_format=ProviderFormat.SAKURA,
        default_base_url="http://127.0.0.1:5000/v1",
        hint_models=("Sakura-14B-Qwen2.5-v1.0",),
        supports_fetch_model_list=True,
        recommended_defaults=RecommendedDefaults(
            timeout_seconds=600.0,
            concurrency_limit=0,
            rpm_limit=0,
            max_output_tokens=2048,
            temperature=0.1,
        ),
        field_hints=_SAKURA_HINTS,
    ),
    ProviderTemplate(
        id="custom",
        display_name="Custom",
        provider_format=ProviderFormat.CUSTOM,
        default_base_url="",
        hint_models=(),
        supports_fetch_model_list=False,
        recommended_defaults=RecommendedDefaults(),
        field_hints=_CUSTOM_HINTS,
    ),
)


def list_templates() -> tuple[ProviderTemplate, ...]:
    """Return the immutable provider catalog.

    The order is intentional: most-popular providers first, ``Custom``
    last so the modal's template picker reads top-to-bottom from
    "what most users want" to "I'll fill in everything myself".
    """

    return _TEMPLATES


def get_template(template_id: str) -> ProviderTemplate | None:
    for template in _TEMPLATES:
        if template.id == template_id:
            return template
    return None


__all__ = [
    "FieldHint",
    "ProviderTemplate",
    "RecommendedDefaults",
    "get_template",
    "list_templates",
]
