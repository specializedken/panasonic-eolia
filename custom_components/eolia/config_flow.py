"""Config flow for the Eolia integration.

Cannot use Home Assistant's built-in OAuth2 config-flow helper: Eolia's Auth0 client is a
native-app registration whose redirect_uri is fixed to a custom mobile scheme and strictly
rejects any other value (confirmed live -- see docs/findings.md and the plan file this was
built from). A human has to complete the /authorize browser step by hand at least once;
this flow makes that step as painless as possible (foolproof DevTools instructions, code
pasted back as plain text) rather than assuming any one-time local customization.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EoliaApiClient
from .auth import (
    EoliaAuth,
    async_exchange_code,
    async_exchange_refresh_token,
    async_get_userinfo,
    build_authorize_url,
    generate_pkce_pair,
    generate_state,
    token_data_to_entry_data,
)
from .const import CONF_ACCESS_TOKEN, CONF_EXPIRES_AT, CONF_REFRESH_TOKEN, DOMAIN
from .exceptions import EoliaApiError, EoliaAuthError
from .models import EoliaDevice

_LOGGER = logging.getLogger(__name__)

_CODE_QUERY_RE = re.compile(r"[?&]code=([^&\s]+)")


def _extract_authorization_code(raw: str) -> str | None:
    """Pull an authorization code out of a bare code or a pasted redirect URL.

    Accepts: a bare code, a full `panasonic-eolia://...?code=...` URL (what DevTools'
    Network tab shows), or a plain https URL with a code= query param.
    """
    raw = raw.strip()
    if not raw:
        return None

    if "code=" not in raw:
        # No query string at all -- treat as a bare code if it looks like one.
        if " " not in raw and "://" not in raw:
            return raw
        return None

    parsed = urlparse(raw)
    if parsed.query:
        codes = parse_qs(parsed.query).get("code")
        if codes:
            return codes[0]

    # Fallback: some custom-scheme URLs don't parse cleanly with urlparse.
    match = _CODE_QUERY_RE.search(raw)
    if match:
        return unquote(match.group(1))

    return None


def _build_title(devices: list[EoliaDevice]) -> str:
    if not devices:
        return "Eolia"
    if len(devices) == 1:
        return f"Eolia ({devices[0].nickname})"
    return f"Eolia ({len(devices)} devices)"


class EoliaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eolia."""

    VERSION = 1

    def __init__(self) -> None:
        self._code_verifier: str | None = None
        self._code_challenge: str | None = None
        self._state: str | None = None

    # -- entry points ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step: let the user pick a login method."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["browser_pkce", "paste_refresh_token"],
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Entered automatically when a refresh fails with invalid_grant."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["browser_pkce", "paste_refresh_token"],
        )

    # -- browser + PKCE path -------------------------------------------------------------

    async def async_step_browser_pkce(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Build the /authorize URL and collect the code pasted back by the user."""
        errors: dict[str, str] = {}

        if self._code_verifier is None:
            self._code_verifier, self._code_challenge = generate_pkce_pair()
            self._state = generate_state()

        if user_input is not None:
            code = _extract_authorization_code(user_input["authorization_response"])
            if code is None:
                errors["base"] = "invalid_code"
            else:
                session = async_get_clientsession(self.hass)
                try:
                    token_data = await async_exchange_code(
                        session, code=code, code_verifier=self._code_verifier
                    )
                except EoliaAuthError as err:
                    _LOGGER.debug("Authorization code exchange failed: %s", err)
                    errors["base"] = "code_exchange_failed"
                else:
                    result = await self._async_finish_login(token_data)
                    if result is not None:
                        return result
                    errors["base"] = "cannot_connect"

        authorize_url = build_authorize_url(
            code_challenge=self._code_challenge, state=self._state
        )
        return self.async_show_form(
            step_id="browser_pkce",
            data_schema=vol.Schema({vol.Required("authorization_response"): str}),
            description_placeholders={"authorize_url": authorize_url},
            errors=errors,
        )

    # -- paste-a-refresh-token fallback path ---------------------------------------------

    async def async_step_paste_refresh_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fallback / headless path: accept an already-obtained refresh_token directly."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                token_data = await async_exchange_refresh_token(
                    session, refresh_token=user_input["refresh_token"]
                )
            except EoliaAuthError as err:
                _LOGGER.debug("Refresh token validation failed: %s", err)
                errors["base"] = "invalid_refresh_token"
            else:
                result = await self._async_finish_login(token_data)
                if result is not None:
                    return result
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="paste_refresh_token",
            data_schema=vol.Schema({vol.Required("refresh_token"): str}),
            errors=errors,
        )

    # -- shared tail: validate token, discover devices, create/update the entry ---------

    async def _async_finish_login(
        self, token_data: dict[str, Any]
    ) -> ConfigFlowResult | None:
        """Validate the new tokens, set the unique ID, and create/update the entry.

        Returns None (not a ConfigFlowResult) on a recoverable failure so the caller can
        redisplay its own form with an error instead of aborting the whole flow.
        """
        entry_data = token_data_to_entry_data(token_data)
        session = async_get_clientsession(self.hass)

        try:
            userinfo = await async_get_userinfo(
                session, access_token=entry_data[CONF_ACCESS_TOKEN]
            )
        except EoliaAuthError as err:
            _LOGGER.debug("Failed to fetch userinfo: %s", err)
            return None

        member_user_id = userinfo.get(
            "https://club.panasonic.jp/userinfo/app_metadata", {}
        ).get("member_user_id")
        if not member_user_id:
            _LOGGER.debug("userinfo response missing member_user_id: %s", userinfo)
            return None

        await self.async_set_unique_id(member_user_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch()
        else:
            self._abort_if_unique_id_configured()

        auth = EoliaAuth(
            session,
            access_token=entry_data[CONF_ACCESS_TOKEN],
            refresh_token=entry_data[CONF_REFRESH_TOKEN],
            expires_at=entry_data[CONF_EXPIRES_AT],
        )
        api = EoliaApiClient(session, auth)
        try:
            devices = await api.async_get_devices()
        except (EoliaApiError, EoliaAuthError) as err:
            _LOGGER.debug("Failed to fetch device list during setup: %s", err)
            return None

        title = _build_title(devices)

        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=entry_data
            )
        return self.async_create_entry(title=title, data=entry_data)
