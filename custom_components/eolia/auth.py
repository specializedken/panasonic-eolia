"""Auth0 Authorization Code + PKCE handling for the Eolia integration.

See docs/findings.md's "The one hard external constraint" -- Eolia's Auth0 client is a native
mobile app registration with a fixed redirect_uri we don't control, confirmed live to
strictly reject any other redirect_uri. The authorization_code leg therefore can't be
automated end-to-end; a human completes the /authorize browser step once per login
(config_flow.py drives this). The refresh_token leg has no such constraint and is fully
automated by this module.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError, ClientSession

from .const import (
    AUTH0_AUDIENCE,
    AUTH0_AUTHORIZE_URL,
    AUTH0_CLIENT_ID,
    AUTH0_REDIRECT_URI,
    AUTH0_SCOPE,
    AUTH0_TOKEN_URL,
    AUTH0_USERINFO_URL,
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    TOKEN_REFRESH_MARGIN_SECONDS,
)
from .exceptions import EoliaAuthError

_LOGGER = logging.getLogger(__name__)


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a (code_verifier, code_challenge) pair per RFC 7636."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def generate_state() -> str:
    """Generate an opaque state value for CSRF protection on the /authorize round trip."""
    return secrets.token_hex(16)


def build_authorize_url(*, code_challenge: str, state: str) -> str:
    """Build the /authorize URL the user must open in a browser."""
    params = {
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": AUTH0_REDIRECT_URI,
        "scope": AUTH0_SCOPE,
        "audience": AUTH0_AUDIENCE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTH0_AUTHORIZE_URL}?{urlencode(params)}"


async def _async_post_token(session: ClientSession, payload: dict[str, str]) -> dict[str, Any]:
    """POST to the Auth0 token endpoint and return the parsed JSON, raising EoliaAuthError on failure."""
    try:
        async with session.post(AUTH0_TOKEN_URL, json=payload) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                error = body.get("error", "unknown_error")
                description = body.get("error_description", "")
                raise EoliaAuthError(
                    f"Auth0 token request failed: {error} ({description})",
                    invalid_grant=error == "invalid_grant",
                )
            return body
    except ClientError as err:
        raise EoliaAuthError(f"Network error talking to Auth0: {err}") from err


async def async_exchange_code(
    session: ClientSession, *, code: str, code_verifier: str
) -> dict[str, Any]:
    """Exchange an authorization code for tokens."""
    return await _async_post_token(
        session,
        {
            "grant_type": "authorization_code",
            "client_id": AUTH0_CLIENT_ID,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": AUTH0_REDIRECT_URI,
        },
    )


async def async_exchange_refresh_token(
    session: ClientSession, *, refresh_token: str
) -> dict[str, Any]:
    """Exchange a refresh token for a new token set."""
    return await _async_post_token(
        session,
        {
            "grant_type": "refresh_token",
            "client_id": AUTH0_CLIENT_ID,
            "refresh_token": refresh_token,
        },
    )


async def async_get_userinfo(session: ClientSession, *, access_token: str) -> dict[str, Any]:
    """Fetch the Auth0 /userinfo claims for the given access token."""
    try:
        async with session.get(
            AUTH0_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        ) as resp:
            if resp.status >= 400:
                raise EoliaAuthError(f"Failed to fetch userinfo: HTTP {resp.status}")
            return await resp.json(content_type=None)
    except ClientError as err:
        raise EoliaAuthError(f"Network error fetching userinfo: {err}") from err


def token_data_to_entry_data(token_data: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw Auth0 token response into the shape stored in ConfigEntry.data."""
    expires_in = token_data.get("expires_in", 0)
    return {
        CONF_ACCESS_TOKEN: token_data["access_token"],
        CONF_REFRESH_TOKEN: token_data.get("refresh_token"),
        CONF_EXPIRES_AT: time.time() + expires_in,
    }


class EoliaAuth:
    """Holds the current token set and refreshes it on demand.

    Auth0's refresh-token rotation behavior for this tenant isn't confirmed, so every
    refresh keeps the previous refresh_token unless the response includes a new one.
    """

    def __init__(
        self,
        session: ClientSession,
        *,
        access_token: str,
        refresh_token: str,
        expires_at: float,
        on_tokens_updated: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = expires_at
        self._on_tokens_updated = on_tokens_updated
        self._lock = asyncio.Lock()

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing first if it's close to expiring."""
        async with self._lock:
            if time.time() >= self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
                await self._async_refresh()
            return self._access_token

    async def async_force_refresh(self) -> str:
        """Force a refresh regardless of expiry (used after a 401/403 from the API)."""
        async with self._lock:
            await self._async_refresh()
            return self._access_token

    async def _async_refresh(self) -> None:
        _LOGGER.debug("Refreshing Eolia access token")
        token_data = await async_exchange_refresh_token(
            self._session, refresh_token=self._refresh_token
        )
        entry_data = token_data_to_entry_data(token_data)
        self._access_token = entry_data[CONF_ACCESS_TOKEN]
        if entry_data[CONF_REFRESH_TOKEN]:
            self._refresh_token = entry_data[CONF_REFRESH_TOKEN]
        self._expires_at = entry_data[CONF_EXPIRES_AT]
        if self._on_tokens_updated is not None:
            await self._on_tokens_updated(
                {
                    CONF_ACCESS_TOKEN: self._access_token,
                    CONF_REFRESH_TOKEN: self._refresh_token,
                    CONF_EXPIRES_AT: self._expires_at,
                }
            )
