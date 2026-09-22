"""Tests for auth.py's PKCE helpers and Auth0 token exchange/refresh.

All token values here are fabricated dummies -- never anything from the real
.eolia_tokens.json (see CLAUDE.md). Uses HA's `aioclient_mock` fixture rather than a
real aiohttp session -- see conftest.py's note on why.
"""

from __future__ import annotations

import time

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.eolia.auth import (
    EoliaAuth,
    async_exchange_code,
    async_exchange_refresh_token,
    build_authorize_url,
    generate_pkce_pair,
    generate_state,
)
from custom_components.eolia.const import AUTH0_TOKEN_URL, TOKEN_REFRESH_MARGIN_SECONDS
from custom_components.eolia.exceptions import EoliaAuthError


def test_generate_pkce_pair_is_well_formed():
    verifier, challenge = generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert "=" not in challenge  # base64url, no padding per RFC 7636


def test_build_authorize_url_carries_pkce_challenge_and_state():
    _, challenge = generate_pkce_pair()
    state = generate_state()
    url = build_authorize_url(code_challenge=challenge, state=state)
    assert "code_challenge_method=S256" in url
    assert f"state={state}" in url
    assert "response_type=code" in url


async def test_exchange_code_success(hass, aioclient_mock):
    aioclient_mock.post(
        AUTH0_TOKEN_URL,
        json={
            "access_token": "FAKE_AT",
            "refresh_token": "FAKE_RT",
            "expires_in": 1209600,
        },
    )
    session = async_get_clientsession(hass)
    token_data = await async_exchange_code(
        session, code="FAKE_CODE", code_verifier="FAKE_VERIFIER"
    )
    assert token_data["access_token"] == "FAKE_AT"


async def test_exchange_code_invalid_grant_raises_with_flag_set(hass, aioclient_mock):
    aioclient_mock.post(
        AUTH0_TOKEN_URL,
        status=403,
        json={"error": "invalid_grant", "error_description": "expired code"},
    )
    session = async_get_clientsession(hass)
    with pytest.raises(EoliaAuthError) as exc_info:
        await async_exchange_code(
            session, code="FAKE_CODE", code_verifier="FAKE_VERIFIER"
        )
    assert exc_info.value.invalid_grant is True


async def test_exchange_refresh_token_non_invalid_grant_error_does_not_set_flag(
    hass, aioclient_mock
):
    aioclient_mock.post(
        AUTH0_TOKEN_URL,
        status=500,
        json={"error": "server_error", "error_description": "oops"},
    )
    session = async_get_clientsession(hass)
    with pytest.raises(EoliaAuthError) as exc_info:
        await async_exchange_refresh_token(session, refresh_token="FAKE_RT")
    assert exc_info.value.invalid_grant is False


async def test_get_access_token_skips_refresh_when_well_within_expiry(hass, aioclient_mock):
    session = async_get_clientsession(hass)
    auth = EoliaAuth(
        session,
        access_token="STILL_FRESH",
        refresh_token="FAKE_RT",
        expires_at=time.time() + TOKEN_REFRESH_MARGIN_SECONDS + 3600,
    )
    token = await auth.async_get_access_token()
    assert token == "STILL_FRESH"


async def test_get_access_token_refreshes_within_margin_and_notifies_callback(
    hass, aioclient_mock
):
    updates: list[dict] = []

    async def on_tokens_updated(data: dict) -> None:
        updates.append(data)

    aioclient_mock.post(
        AUTH0_TOKEN_URL,
        json={
            "access_token": "NEW_AT",
            "refresh_token": "NEW_RT",
            "expires_in": 1209600,
        },
    )
    session = async_get_clientsession(hass)
    auth = EoliaAuth(
        session,
        access_token="OLD_AT",
        refresh_token="OLD_RT",
        expires_at=time.time() + TOKEN_REFRESH_MARGIN_SECONDS - 10,
        on_tokens_updated=on_tokens_updated,
    )
    token = await auth.async_get_access_token()
    assert token == "NEW_AT"
    assert updates == [
        {
            "access_token": "NEW_AT",
            "refresh_token": "NEW_RT",
            "expires_at": updates[0]["expires_at"],
        }
    ]


async def test_refresh_keeps_previous_refresh_token_if_response_omits_one(
    hass, aioclient_mock
):
    """Rotation behavior for this tenant isn't confirmed -- never drop a good token."""
    aioclient_mock.post(
        AUTH0_TOKEN_URL, json={"access_token": "NEW_AT", "expires_in": 1209600}
    )
    session = async_get_clientsession(hass)
    auth = EoliaAuth(
        session, access_token="OLD_AT", refresh_token="OLD_RT", expires_at=0
    )
    await auth.async_force_refresh()
    assert auth._refresh_token == "OLD_RT"


async def test_refresh_invalid_grant_propagates_for_reauth_handling(hass, aioclient_mock):
    aioclient_mock.post(
        AUTH0_TOKEN_URL,
        status=403,
        json={"error": "invalid_grant", "error_description": "revoked"},
    )
    session = async_get_clientsession(hass)
    auth = EoliaAuth(
        session, access_token="OLD_AT", refresh_token="REVOKED_RT", expires_at=0
    )
    with pytest.raises(EoliaAuthError) as exc_info:
        await auth.async_force_refresh()
    assert exc_info.value.invalid_grant is True
