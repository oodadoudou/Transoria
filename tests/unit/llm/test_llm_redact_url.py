"""Tests for ``_redact_url`` — never let a Google API key leak through
an error message or log line."""

from __future__ import annotations

from transoria.llm.client import _redact_url


def test_redact_replaces_key_query_with_stars() -> None:
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0:generateContent?key=AIzaSyABCDEF1234567890"
    assert "AIzaSyABCDEF1234567890" not in _redact_url(url)
    assert "key=***" in _redact_url(url)


def test_redact_handles_key_after_other_query_param() -> None:
    url = "https://example.com/api?foo=bar&key=secret&baz=qux"
    redacted = _redact_url(url)
    assert "secret" not in redacted
    assert "&key=***" in redacted
    assert "foo=bar" in redacted
    assert "baz=qux" in redacted


def test_redact_leaves_other_urls_alone() -> None:
    url = "https://api.openai.com/v1/chat/completions"
    assert _redact_url(url) == url


def test_redact_handles_empty_key_value() -> None:
    url = "https://example.com/api?key="
    assert _redact_url(url) == "https://example.com/api?key=***"
