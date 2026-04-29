"""Model profile library.

A model profile is the user-facing wrapper around the LLM client's
:class:`transoria.llm.config.ModelConfig`. Profiles persist as a JSON
array of bodies (everything except API keys), and API keys live in a
separate ignored file so cloud sync products and version control never
see the secrets.

Public API:

- :class:`ModelProfileStore` — atomic loader/saver with seed defaults.
- :func:`default_profiles` — the four seeded entries (DeepSeek,
  Anthropic, Google, OpenAI) used on first run.
- :func:`mask_api_keys` — helper for redacting keys before serialization
  to the frontend.
"""

from transoria.model_profiles.defaults import (
    DEFAULT_PROFILE_IDS,
    default_profiles,
)
from transoria.model_profiles.store import (
    ApiKeyStatus,
    ModelProfileStore,
    mask_api_keys,
)
from transoria.model_profiles.templates import (
    FieldHint,
    ProviderTemplate,
    RecommendedDefaults,
    get_template,
    list_templates,
)

__all__ = [
    "ApiKeyStatus",
    "DEFAULT_PROFILE_IDS",
    "FieldHint",
    "ModelProfileStore",
    "ProviderTemplate",
    "RecommendedDefaults",
    "default_profiles",
    "get_template",
    "list_templates",
    "mask_api_keys",
]
