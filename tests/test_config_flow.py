"""Tests for config_flow.py's authorization-code extraction.

This is the parser behind the config flow's single text field, which per
docs/phase1-plan.md must accept either a bare code or the full failed-redirect URL a user
copies out of browser DevTools (the primary, zero-setup login path -- see "The one hard
external constraint" in the plan).
"""

from __future__ import annotations

from custom_components.eolia.config_flow import _extract_authorization_code, _extract_state


def test_extracts_from_full_custom_scheme_redirect_url():
    raw = (
        "panasonic-eolia://auth.digital.panasonic.com/android/com.panasonic.SmartRAC/"
        "callback?code=abc123&state=xyz"
    )
    assert _extract_authorization_code(raw) == "abc123"


def test_extracts_from_plain_https_url():
    raw = "https://example.com/callback?state=xyz&code=abc123"
    assert _extract_authorization_code(raw) == "abc123"


def test_accepts_bare_code():
    assert _extract_authorization_code("abc123") == "abc123"


def test_strips_surrounding_whitespace():
    assert _extract_authorization_code("  abc123  \n") == "abc123"


def test_empty_string_returns_none():
    assert _extract_authorization_code("") is None
    assert _extract_authorization_code("   ") is None


def test_url_with_no_code_param_returns_none():
    raw = "panasonic-eolia://auth.digital.panasonic.com/callback?error=access_denied"
    assert _extract_authorization_code(raw) is None


def test_url_encoded_code_is_decoded():
    raw = "https://example.com/callback?code=abc%2F123"
    assert _extract_authorization_code(raw) == "abc/123"


def test_extract_state_from_redirect_url():
    raw = "panasonic-eolia://auth.digital.panasonic.com/callback?code=abc123&state=xyz"
    assert _extract_state(raw) == "xyz"


def test_extract_state_is_none_for_bare_code_or_stateless_url():
    assert _extract_state("abc123") is None
    assert _extract_state("https://example.com/callback?code=abc123") is None
