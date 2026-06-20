"""Tests for ``transoria.model_profiles.templates``.

The catalog is read-only and immutable. Tests assert structural
invariants (uniqueness, required fields, Custom-template rules) so a
future provider addition cannot accidentally break the modal.
"""

from __future__ import annotations

from transoria.bridge import BridgeRouter
from transoria.bridge.handlers.model_templates import register
from transoria.llm.config import ProviderFormat
from transoria.model_profiles.templates import (
    FieldHint,
    ProviderTemplate,
    RecommendedDefaults,
    get_template,
    list_templates,
)


def test_catalog_is_non_empty_and_includes_custom():
    templates = list_templates()
    assert len(templates) >= 4
    ids = [t.id for t in templates]
    assert ids[-1] == "custom", "Custom template must come last"
    assert "openai" in ids
    assert "anthropic" in ids


def test_catalog_ids_are_unique():
    ids = [t.id for t in list_templates()]
    assert len(ids) == len(set(ids))


def test_provider_templates_have_recommended_values():
    """Every non-Custom template renders the "Recommended for X"
    row, so each must ship at least one field hint with a non-empty
    ``recommended_value``. Source URLs are bonus — local providers
    like Sakura may legitimately omit them."""

    for template in list_templates():
        if template.id == "custom":
            continue
        has_recommendation = any(
            hint.recommended_value for hint in template.field_hints.values()
        )
        assert has_recommendation, (
            f"Template {template.id!r} should have at least one "
            "field hint with a non-empty recommended_value"
        )


def test_cloud_provider_templates_cite_a_source_url():
    """OpenAI / Anthropic / Google / DeepSeek / Volcengine Ark publish
    rate-limit docs; their templates cite at least one. Local
    providers (Sakura) and Custom are exempt."""

    cloud_ids = {"openai", "anthropic", "google", "deepseek", "volcengine-ark"}
    for template in list_templates():
        if template.id not in cloud_ids:
            continue
        has_source = any(
            hint.source_url for hint in template.field_hints.values()
        )
        assert has_source, (
            f"Cloud template {template.id!r} should cite at least "
            "one source URL on a field hint"
        )


def test_custom_template_hints_have_no_recommendations():
    """Architecture § 3.4: the Custom template renders only the
    description row in (?) tooltips — no Recommended, no Source."""

    custom = get_template("custom")
    assert custom is not None
    assert custom.field_hints, "Custom template must still ship descriptions"
    for hint in custom.field_hints.values():
        assert hint.recommended_value == ""
        assert hint.source_url is None


def test_custom_template_uses_custom_provider_format():
    custom = get_template("custom")
    assert custom is not None
    assert custom.provider_format is ProviderFormat.CUSTOM
    assert custom.default_base_url == ""
    assert custom.hint_models == ()
    assert custom.supports_fetch_model_list is False


def test_get_template_returns_none_for_unknown_id():
    assert get_template("nonexistent-provider") is None


def test_recommended_defaults_serializes_thinking_level_as_string():
    defaults = RecommendedDefaults()
    payload = defaults.to_dict()
    assert isinstance(payload["thinking_level"], str)


def test_field_hint_defaults_are_empty():
    """Default-constructed FieldHint represents the Custom-style
    description-only popover."""

    hint = FieldHint(description_key="modelHints.timeout")
    assert hint.recommended_value == ""
    assert hint.source_url is None


def test_provider_template_to_dict_round_trips_field_hints():
    template = ProviderTemplate(
        id="probe",
        display_name="Probe",
        provider_format=ProviderFormat.OPENAI,
        default_base_url="https://example.com",
        hint_models=("a", "b"),
        supports_fetch_model_list=True,
        recommended_defaults=RecommendedDefaults(rpm_limit=42),
        field_hints={
            "rpm_limit": FieldHint(
                description_key="modelHints.rpm",
                recommended_value="42 RPM",
                source_url="https://example.com/docs",
            ),
        },
    )
    payload = template.to_dict()
    assert payload["id"] == "probe"
    assert payload["recommended_defaults"]["rpm_limit"] == 42
    rpm_hint = payload["field_hints"]["rpm_limit"]
    assert rpm_hint["description_key"] == "modelHints.rpm"
    assert rpm_hint["recommended_value"] == "42 RPM"
    assert rpm_hint["source_url"] == "https://example.com/docs"


def test_bridge_handler_returns_full_catalog():
    router = BridgeRouter()
    register(router)

    response = router.call("model_templates.list", {})

    assert "templates" in response
    payload_ids = [t["id"] for t in response["templates"]]
    assert payload_ids == [t.id for t in list_templates()]
    # Spot-check shape.
    openai = next(t for t in response["templates"] if t["id"] == "openai")
    assert openai["provider_format"] == "openai"
    assert openai["supports_fetch_model_list"] is True
    assert "rpm_limit" in openai["recommended_defaults"]
    assert "rpm_limit" in openai["field_hints"]
