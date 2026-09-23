"""Eolia cloud API client.

Wire protocol details (base URL, headers, the X-Eolia-Date clock-skew check, the exact
control-request field set) are all taken from findings.md's live-confirmed contract.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientSession

from .auth import EoliaAuth
from .const import (
    API_BASE_URL,
    EOLIA_DATE_FORMAT,
    EOLIA_DATE_TIMEZONE,
    ERROR_CODE_CLOCK_SKEW,
    ERROR_CODE_DEVICE_LOCKED,
)
from .exceptions import (
    EoliaApiError,
    EoliaClockSkewError,
    EoliaDeviceLockedError,
    EoliaNetworkError,
)
from .models import EoliaCustomSettings, EoliaDevice, EoliaStatus

_LOGGER = logging.getLogger(__name__)


class EoliaApiClient:
    """Thin async wrapper around the Eolia device-control API."""

    def __init__(self, session: ClientSession, auth: EoliaAuth) -> None:
        self._session = session
        self._auth = auth

    async def async_get_devices(self) -> list[EoliaDevice]:
        """GET /devices."""
        data = await self._async_request("GET", "/devices")
        return [EoliaDevice.from_dict(item) for item in data.get("ac_list", [])]

    async def async_get_status(self, appliance_id: str) -> EoliaStatus:
        """GET /devices/{appliance_id}/status."""
        data = await self._async_request("GET", self._status_path(appliance_id))
        return EoliaStatus.from_dict(data)

    async def async_set_status(
        self, appliance_id: str, payload: dict[str, Any]
    ) -> EoliaStatus:
        """PUT /devices/{appliance_id}/status.

        `payload` must already be the exact fixed field set from
        EoliaStatus.to_control_fields() plus silence_control -- this method does not
        validate or filter it, since building that payload correctly is coordinator.py's
        responsibility (it needs the previous state to do a read-modify-write).
        """
        data = await self._async_request(
            "PUT", self._status_path(appliance_id), json_body=payload
        )
        return EoliaStatus.from_dict(data)

    async def async_get_functions(self, product_code: str) -> dict[str, bool]:
        """GET /products/{product_code}/functions -- which features this model supports.

        Returns {function_id: supported}. The official app only offers a mode/feature when
        its flag is true here (found in the decompiled APK, a9/d.java + i9/y.java).
        """
        data = await self._async_request(
            "GET", f"/products/{quote(product_code, safe='')}/functions"
        )
        return {
            item["function_id"]: bool(item.get("function_value"))
            for item in data.get("ac_function_list", [])
            if item.get("function_id")
        }

    async def async_get_custom_settings(self, appliance_id: str) -> EoliaCustomSettings:
        """GET /devices/{appliance_id}/customsettings -- KeepMode's double-temp range."""
        data = await self._async_request("GET", self._custom_settings_path(appliance_id))
        return EoliaCustomSettings.from_dict(data)

    async def async_set_custom_settings(
        self, appliance_id: str, payload: dict[str, Any]
    ) -> EoliaCustomSettings:
        """PUT /devices/{appliance_id}/customsettings.

        `payload` must already be the exact fixed field set from
        EoliaCustomSettings.to_control_fields() plus any overrides -- same
        read-modify-write contract as async_set_status(), owned by coordinator.py.
        """
        data = await self._async_request(
            "PUT", self._custom_settings_path(appliance_id), json_body=payload
        )
        return EoliaCustomSettings.from_dict(data)

    @staticmethod
    def _status_path(appliance_id: str) -> str:
        return f"/devices/{quote(appliance_id, safe='')}/status"

    @staticmethod
    def _custom_settings_path(appliance_id: str) -> str:
        return f"/devices/{quote(appliance_id, safe='')}/customsettings"

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        # Must be JST regardless of the HA host's own timezone -- the server enforces a
        # +/-5 minute clock-skew check against this. Confirmed live 2026-09-23.
        now = datetime.now(ZoneInfo(EOLIA_DATE_TIMEZONE))
        return {
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "X-Eolia-Date": now.strftime(EOLIA_DATE_FORMAT),
            "Authorization": f"Bearer {access_token}",
        }

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        _retried_auth: bool = False,
    ) -> dict[str, Any]:
        access_token = await self._auth.async_get_access_token()
        url = f"{API_BASE_URL}{path}"
        # Full request/response logging at DEBUG (never the Authorization header/token) --
        # added 2026-09-23 to watch live HA<->API traffic while testing real entity
        # interactions for combinations that produce a server-side error. Enable via HA's
        # logger integration: custom_components.eolia.api: debug.
        _LOGGER.debug("Eolia API request: %s %s body=%s", method, path, json_body)
        try:
            async with self._session.request(
                method, url, headers=self._headers(access_token), json=json_body
            ) as resp:
                if resp.status in (401, 403) and not _retried_auth:
                    _LOGGER.debug(
                        "Eolia API returned %s, forcing token refresh and retrying once",
                        resp.status,
                    )
                    await self._auth.async_force_refresh()
                    return await self._async_request(
                        method, path, json_body=json_body, _retried_auth=True
                    )

                try:
                    body: dict[str, Any] = await resp.json(content_type=None)
                except ValueError:
                    body = {}

                if resp.status >= 400:
                    code = body.get("code")
                    message = body.get("message", "")
                    _KNOWN_CODES = (ERROR_CODE_CLOCK_SKEW, ERROR_CODE_DEVICE_LOCKED)
                    if code is None:
                        _LOGGER.warning(
                            "Eolia API error with no recognizable code: status=%s body=%s",
                            resp.status,
                            body,
                        )
                    elif code not in _KNOWN_CODES:
                        # Log anything not specially handled below verbatim so it can be
                        # folded back into findings.md/const.py later.
                        _LOGGER.debug(
                            "Eolia API error code=%s message=%s (undocumented code, "
                            "consider adding to findings.md)",
                            code,
                            message,
                        )
                    _LOGGER.debug(
                        "Eolia API response: %s %s -> %s body=%s",
                        method,
                        path,
                        resp.status,
                        body,
                    )
                    if code == ERROR_CODE_CLOCK_SKEW:
                        raise EoliaClockSkewError(resp.status, code, message)
                    if code == ERROR_CODE_DEVICE_LOCKED:
                        raise EoliaDeviceLockedError(resp.status, code, message)
                    raise EoliaApiError(resp.status, code, message)

                _LOGGER.debug(
                    "Eolia API response: %s %s -> %s body=%s", method, path, resp.status, body
                )
                return body
        except ClientError as err:
            raise EoliaNetworkError(f"Network error calling {method} {path}: {err}") from err
