"""Settings schema and frozen defaults.

The schema mirrors ``AllSettings`` in the bridge contract. Defaults live
here so both the initial load (empty file) and ``settings.reset_module``
return identical values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal, Mapping

from transoria.domain import Language

SettingsModule = Literal["app", "translation", "glossary", "replacement"]

ChineseOutputForm = Literal["simplified", "traditional"]
Theme = Literal["light", "dark", "system"]
InterfaceLanguage = Literal["en", "zh"]


@dataclass(frozen=True)
class AppSettings:
    interface_language: InterfaceLanguage = "en"
    theme: Theme = "system"
    ui_scale: float = 1.0
    proxy_url: str = ""
    active_translation_model_id: str | None = None
    active_glossary_model_id: str | None = None
    active_translation_prompt_id: str | None = None
    active_glossary_prompt_id: str | None = None


@dataclass(frozen=True)
class TranslationSettings:
    input_folder: str = ""
    output_folder: str = ""
    source_language: str = Language.KOREAN.value
    target_language: str = Language.CHINESE_SIMPLIFIED.value
    chinese_output_form: ChineseOutputForm = "simplified"
    bilingual_enabled: bool = False
    bilingual_dedupe_identical: bool = True
    bilingual_subfolder_name: str = "bilingual outputs"
    context_lines: int = 25
    auto_open_output_folder: bool = False


@dataclass(frozen=True)
class GlossarySettings:
    input_folder: str = ""
    output_folder: str = ""
    source_language: str = Language.KOREAN.value
    target_language: str = Language.CHINESE_SIMPLIFIED.value
    chinese_output_form: ChineseOutputForm = "simplified"
    reference_examples_per_term: int = 20
    max_term_display_length: int = 32
    minimum_frequency: int = 1
    chunk_token_limit: int = 4000
    merge_folder_glossary: bool = True
    keep_identical_src_dst: bool = False
    auto_open_output_folder: bool = False


@dataclass(frozen=True)
class ReplacementSettings:
    input_folder: str = ""
    output_folder: str = ""
    allow_same_folder: bool = False
    output_naming_suffix: str = "Replaced"
    overwrite_existing: bool = False
    apply_to_epub_titles: bool = True
    stop_on_first_error: bool = False


@dataclass(frozen=True)
class AllSettings:
    app: AppSettings = AppSettings()
    translation: TranslationSettings = TranslationSettings()
    glossary: GlossarySettings = GlossarySettings()
    replacement: ReplacementSettings = ReplacementSettings()

    def to_dict(self) -> dict[str, dict[str, object]]:
        return {
            "app": asdict(self.app),
            "translation": asdict(self.translation),
            "glossary": asdict(self.glossary),
            "replacement": asdict(self.replacement),
        }

    def with_module(
        self,
        module: SettingsModule,
        value: AppSettings
        | TranslationSettings
        | GlossarySettings
        | ReplacementSettings,
    ) -> "AllSettings":
        if module == "app" and isinstance(value, AppSettings):
            return replace(self, app=value)
        if module == "translation" and isinstance(value, TranslationSettings):
            return replace(self, translation=value)
        if module == "glossary" and isinstance(value, GlossarySettings):
            return replace(self, glossary=value)
        if module == "replacement" and isinstance(value, ReplacementSettings):
            return replace(self, replacement=value)
        raise ValueError(
            f"Module {module!r} does not match value type {type(value).__name__}"
        )


def default_settings() -> AllSettings:
    """Return the frozen default bundle."""

    return AllSettings()


_MODULE_TYPES: dict[SettingsModule, type] = {
    "app": AppSettings,
    "translation": TranslationSettings,
    "glossary": GlossarySettings,
    "replacement": ReplacementSettings,
}


def default_module_settings(
    module: SettingsModule,
) -> AppSettings | TranslationSettings | GlossarySettings | ReplacementSettings:
    """Return the default for one module."""

    cls = _MODULE_TYPES.get(module)
    if cls is None:
        raise ValueError(f"Unknown settings module: {module!r}")
    return cls()


def merge_module(
    current: AppSettings | TranslationSettings | GlossarySettings | ReplacementSettings,
    patch: Mapping[str, object],
) -> AppSettings | TranslationSettings | GlossarySettings | ReplacementSettings:
    """Apply a partial patch onto the current module value.

    Unknown keys raise ``ValueError`` so the bridge can return
    ``bridge.invalid_argument`` with the offending field name. Type
    mismatches also raise so the wire format stays trustworthy.
    """

    valid_fields = {f.name for f in current.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    overrides: dict[str, object] = {}
    for key, value in patch.items():
        if key not in valid_fields:
            raise ValueError(f"Unknown settings field: {key!r}")
        overrides[key] = _coerce(current, key, value)
    return replace(current, **overrides)


def _coerce(
    current: AppSettings | TranslationSettings | GlossarySettings | ReplacementSettings,
    key: str,
    value: object,
) -> object:
    """Best-effort coercion for primitive JSON inputs.

    JSON has no integer/float distinction, and the frontend may pass numeric
    strings (e.g. from text inputs). We coerce to the dataclass field's
    declared type when that is one of ``int``, ``float``, ``bool``, or
    ``str``. Optional string fields accept ``None``. Anything else passes
    through untouched and the dataclass replace will surface mismatches.
    """

    field_type = current.__dataclass_fields__[key].type  # type: ignore[attr-defined]
    annotation = field_type if isinstance(field_type, str) else field_type.__name__

    if "int" == annotation:
        if isinstance(value, bool) or value is None:
            raise ValueError(f"Field {key!r} expects an integer, got {value!r}")
        return int(value)  # type: ignore[arg-type]
    if "float" == annotation:
        if value is None or isinstance(value, bool):
            raise ValueError(f"Field {key!r} expects a number, got {value!r}")
        return float(value)  # type: ignore[arg-type]
    if "bool" == annotation:
        if not isinstance(value, bool):
            raise ValueError(f"Field {key!r} expects a boolean, got {value!r}")
        return value
    if "str" == annotation:
        if not isinstance(value, str):
            raise ValueError(f"Field {key!r} expects a string, got {value!r}")
        return value
    return value


__all__ = [
    "AllSettings",
    "AppSettings",
    "ChineseOutputForm",
    "GlossarySettings",
    "InterfaceLanguage",
    "ReplacementSettings",
    "SettingsModule",
    "Theme",
    "TranslationSettings",
    "default_module_settings",
    "default_settings",
    "merge_module",
]
