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
InterfaceLanguage = Literal["en", "zh"]


@dataclass(frozen=True)
class AppSettings:
    interface_language: InterfaceLanguage = "en"
    ui_scale: float = 1.0
    proxy_url: str = ""
    active_translation_model_id: str | None = None
    active_glossary_model_id: str | None = None
    active_translation_prompt_id: str | None = None
    active_glossary_prompt_id: str | None = None
    # Latest release tag the user clicked "later" or "update now" on.
    # The startup update prompt suppresses itself until ``latest_version``
    # diverges from this value, so a confirmed-or-dismissed release
    # never re-nags. Empty = no version skipped yet.
    skipped_update_version: str = ""


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
    # Soft upper bound on preceding source lines bundled with each
    # chunk for cross-chunk pronoun resolution and narrative cohesion.
    # The chunker walks backwards collecting at most this many lines,
    # but stops as soon as a non-empty line doesn't end in sentence-
    # final punctuation — which means in typical novel prose the
    # actual count sent is far smaller than the cap. 25 is generous
    # enough that quality-sensitive users see no clipping while the
    # heuristic keeps token cost bounded.
    context_lines: int = 25
    low_confidence_max_retries: int = 3
    auto_open_output_folder: bool = False
    timeout_seconds: int = 120
    # Glossary entries threaded into TranslationConfig.glossary at run
    # start. Each entry mirrors the backend `GlossaryEntry` shape:
    # ``{src, dst, info, regex, case_sensitive, enabled}``. JSON lists
    # round-trip as Python lists; the field is stored as-is and
    # converted via ``Glossary.from_records`` at task-start time.
    translation_glossary: tuple[dict[str, object], ...] = ()
    # Text-preserve rules: regex/literal patterns whose matches are
    # protected from translation (sent to the model as opaque tokens).
    # Each entry: ``{pattern, note, enabled}``.
    text_preserve_rules: tuple[dict[str, object], ...] = ()
    # Pre-translation replacements: applied to the source text before
    # the LLM sees it. Each entry mirrors ReplacementRule:
    # ``{src, dst, regex, case_sensitive, note, enabled}``.
    pre_replacements: tuple[dict[str, object], ...] = ()
    # Post-translation replacements: applied to the model's output
    # before writeback. Same shape as pre_replacements.
    post_replacements: tuple[dict[str, object], ...] = ()


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
    normalize_widths: bool = True
    auto_open_output_folder: bool = False
    timeout_seconds: int = 120


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

# TranslationSettings fields that hold a tuple of mappings on disk
# but accept a list of mappings on the wire. ``_coerce`` and
# ``_hydrate`` use this set to round-trip them safely.
_TRANSLATION_LIST_OF_MAPPING_FIELDS: frozenset[str] = frozenset(
    {
        "translation_glossary",
        "text_preserve_rules",
        "pre_replacements",
        "post_replacements",
    }
)



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


def merge_module_lenient(
    current: AppSettings | TranslationSettings | GlossarySettings | ReplacementSettings,
    patch: Mapping[str, object],
) -> tuple[
    AppSettings | TranslationSettings | GlossarySettings | ReplacementSettings,
    list[dict[str, str]],
]:
    """Apply each patch entry independently; return ``(merged, rejected)``.

    Unlike ``merge_module`` (single-error abort), this preserves every
    valid field and reports the rejected ones individually. Used by the
    user-facing ``settings.save_partial`` bridge handler so a typo in
    one field doesn't throw away the user's other valid changes — the
    common case where the Settings page has many fields and a single
    bad input would otherwise wipe everything.
    """

    valid_fields = {f.name for f in current.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    rejected: list[dict[str, str]] = []
    overrides: dict[str, object] = {}
    for key, value in patch.items():
        if key not in valid_fields:
            rejected.append({"field": key, "reason": "unknown field"})
            continue
        try:
            overrides[key] = _coerce(current, key, value)
        except (TypeError, ValueError) as exc:
            rejected.append({"field": key, "reason": str(exc)})
            continue
    if not overrides:
        return current, rejected
    try:
        merged = replace(current, **overrides)
    except (TypeError, ValueError) as exc:
        # ``replace`` only raises when a coerced value still doesn't
        # match the dataclass field type — fall back to per-field
        # bisection so the surviving fields still get applied.
        merged = current
        for key, value in overrides.items():
            try:
                merged = replace(merged, **{key: value})
            except (TypeError, ValueError) as inner_exc:
                rejected.append({"field": key, "reason": str(inner_exc)})
        # Note: if every override failed at this stage, ``merged``
        # equals ``current`` and the caller still sees the rejected
        # list, which is the desired UX.
        del exc  # silence linter
    return merged, rejected


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

    # Special-case: list-of-mapping fields on TranslationSettings
    # (glossary, preserve, pre/post replacements). Validate the
    # shape and freeze to a tuple of dicts so the dataclass stays
    # immutable.
    if (
        key in _TRANSLATION_LIST_OF_MAPPING_FIELDS
        and isinstance(current, TranslationSettings)
    ):
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                f"Field {key!r} expects a list of objects, "
                f"got {type(value).__name__}"
            )
        normalized: list[dict[str, object]] = []
        for index, entry in enumerate(value):
            if not isinstance(entry, Mapping):
                raise ValueError(
                    f"Field {key!r} entry {index} must be an object, "
                    f"got {type(entry).__name__}"
                )
            normalized.append(dict(entry))
        return tuple(normalized)


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
    "TranslationSettings",
    "_TRANSLATION_LIST_OF_MAPPING_FIELDS",
    "default_module_settings",
    "default_settings",
    "merge_module",
]
