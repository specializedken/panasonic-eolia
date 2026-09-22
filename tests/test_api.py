"""Tests for api.py: the X-Eolia-Date JST clock-skew requirement, error-code mapping,
and the one-retry-on-401/403 behavior.

Uses HA's own `aioclient_mock` fixture (which patches HA's clientsession factory
directly, matching how the real integration obtains a session in __init__.py) rather
than a library like aioresponses that builds a real aiohttp connector -- a real
connector pulls in aiohttp's aiodns-backed AsyncResolver (HA hardcodes it, see
homeassistant.helpers.aiohttp_client), whose lazily-started background thread races
with pytest-homeassistant-custom-component's per-test thread-leak check.
"""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)
from yarl import URL

import pytest
from freezegun import freeze_time

from custom_components.eolia.api import EoliaApiClient
from custom_components.eolia.const import API_BASE_URL
from custom_components.eolia.exceptions import EoliaApiError, EoliaClockSkewError

_STATUS_URL = f"{API_BASE_URL}/devices/APPLIANCE1/status"


def _make_auth(token: str = "ACCESS_TOKEN") -> AsyncMock:
    auth = AsyncMock()
    auth.async_get_access_token.return_value = token
    return auth


def _sequential_response_side_effect(*responses: AiohttpClientMockResponse):
    """Return a fresh registered response on each successive call to the same mock."""
    remaining = iter(responses)

    async def _side_effect(method, url, data):
        return next(remaining)

    return _side_effect


def test_status_path_url_encodes_appliance_id():
    # Real appliance_ids are base64 and can contain "=", "+", "/".
    path = EoliaApiClient._status_path("EXAMPLEAPPLIANCEID0000000000000000000000000=")
    assert path == "/devices/EXAMPLEAPPLIANCEID0000000000000000000000000%3D/status"


@freeze_time("2026-01-15 03:00:00")  # arbitrary UTC instant, well off JST
def test_headers_x_eolia_date_is_always_jst(monkeypatch):
    # Confirmed live (findings.md): the server enforces a +/-5 minute clock-skew check
    # against X-Eolia-Date, and it must be JST regardless of the HA host's own timezone.
    has_tzset = hasattr(time, "tzset")
    monkeypatch.setenv("TZ", "America/New_York")
    if has_tzset:
        time.tzset()

    try:
        headers = EoliaApiClient._headers("TOKEN")
        expected = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%dT%H:%M:%S")
        assert headers["X-Eolia-Date"] == expected
        assert headers["Authorization"] == "Bearer TOKEN"
    finally:
        # monkeypatch reverts the TZ env var automatically, but the process-wide C
        # library timezone state set by tzset() above would otherwise leak into
        # every later test unless we explicitly reset it here.
        if has_tzset:
            monkeypatch.undo()
            time.tzset()


async def test_get_status_clock_skew_error_maps_to_dedicated_exception(hass, aioclient_mock):
    aioclient_mock.get(
        _STATUS_URL, status=400, json={"code": "E-21291-00002", "message": "clock skew"}
    )
    client = EoliaApiClient(async_get_clientsession(hass), _make_auth())
    with pytest.raises(EoliaClockSkewError):
        await client.async_get_status("APPLIANCE1")


async def test_set_status_generic_application_error_is_not_a_clock_skew_error(
    hass, aioclient_mock
):
    aioclient_mock.put(
        _STATUS_URL, status=400, json={"code": "E-21291-00007", "message": "generic error"}
    )
    client = EoliaApiClient(async_get_clientsession(hass), _make_auth())
    with pytest.raises(EoliaApiError) as exc_info:
        await client.async_set_status("APPLIANCE1", {})
    assert exc_info.value.code == "E-21291-00007"
    assert not isinstance(exc_info.value, EoliaClockSkewError)


async def test_unknown_error_code_still_raises_generic_api_error(hass, aioclient_mock):
    aioclient_mock.get(
        _STATUS_URL, status=400, json={"code": "E-21291-99999", "message": "never seen before"}
    )
    client = EoliaApiClient(async_get_clientsession(hass), _make_auth())
    with pytest.raises(EoliaApiError) as exc_info:
        await client.async_get_status("APPLIANCE1")
    assert exc_info.value.code == "E-21291-99999"


async def test_401_triggers_one_forced_refresh_and_retries_once(hass, aioclient_mock):
    auth = _make_auth("OLD_TOKEN")
    auth.async_force_refresh.return_value = "NEW_TOKEN"
    aioclient_mock.get(
        _STATUS_URL,
        side_effect=_sequential_response_side_effect(
            AiohttpClientMockResponse(
                method="get",
                url=URL(_STATUS_URL),
                status=401,
                json={"code": "E-UNKNOWN", "message": "unauthorized"},
            ),
            AiohttpClientMockResponse(
                method="get",
                url=URL(_STATUS_URL),
                status=200,
                json={"appliance_id": "APPLIANCE1"},
            ),
        ),
    )
    client = EoliaApiClient(async_get_clientsession(hass), auth)
    status = await client.async_get_status("APPLIANCE1")
    auth.async_force_refresh.assert_awaited_once()
    assert status.appliance_id == "APPLIANCE1"


async def test_401_after_retry_still_fails_raises_api_error_not_infinite_loop(
    hass, aioclient_mock
):
    auth = _make_auth("OLD_TOKEN")
    auth.async_force_refresh.return_value = "STILL_BAD_TOKEN"
    aioclient_mock.get(
        _STATUS_URL,
        side_effect=_sequential_response_side_effect(
            AiohttpClientMockResponse(
                method="get",
                url=URL(_STATUS_URL),
                status=401,
                json={"code": "E-UNKNOWN", "message": "unauthorized"},
            ),
            AiohttpClientMockResponse(
                method="get",
                url=URL(_STATUS_URL),
                status=401,
                json={"code": "E-UNKNOWN", "message": "still unauthorized"},
            ),
        ),
    )
    client = EoliaApiClient(async_get_clientsession(hass), auth)
    with pytest.raises(EoliaApiError):
        await client.async_get_status("APPLIANCE1")
    auth.async_force_refresh.assert_awaited_once()
