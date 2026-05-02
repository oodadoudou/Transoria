"""Persisted application settings.

Public surface:

- :class:`AppSettings`, :class:`TranslationSettings`,
  :class:`GlossarySettings`, :class:`ReplacementSettings` — typed module
  schemas matching ``docs/bridge-contract.md``.
- :class:`AllSettings` — bundle of all four.
- :class:`SettingsStore` — atomic JSON-backed loader/saver.
- :func:`default_settings` — frozen defaults used by both load-empty and
  reset paths.
"""

from transoria.settings.defaults import (
    AllSettings,
    AppSettings,
    GlossarySettings,
    ReplacementSettings,
    SettingsModule,
    TranslationSettings,
    default_module_settings,
    default_settings,
)
from transoria.settings.store import SettingsStore

__all__ = [
    "AllSettings",
    "AppSettings",
    "GlossarySettings",
    "ReplacementSettings",
    "SettingsModule",
    "SettingsStore",
    "TranslationSettings",
    "default_module_settings",
    "default_settings",
]
