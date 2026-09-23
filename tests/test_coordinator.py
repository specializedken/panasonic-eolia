"""Tests for coordinator.py's read-modify-write PUT-body contract.

async_set_status() is the only place a PUT body gets built. These tests regression-lock
the exact finding that blocked live testing for a day (see findings.md's "RESOLVED"
section): the outgoing payload must never contain applianceId/humidity and must always
contain silence_control, even though silence_control has no GET readback at all.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.eolia.const import CONTROL_REQUEST_FIELDS, CUSTOM_SETTINGS_REQUEST_FIELDS
from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
from custom_components.eolia.exceptions import EoliaApiError
from custom_components.eolia.models import EoliaCustomSettings, EoliaDevice, EoliaStatus

APPLIANCE_ID = "EXAMPLEAPPLIANCEID0000000000000000000000000="


@pytest.fixture
def device() -> EoliaDevice:
    return EoliaDevice(
        appliance_id=APPLIANCE_ID,
        nickname="Yurt",
        product_code="CS-712DX2-W",
        product_name="Test",
    )


@pytest.fixture
def initial_status(status_response) -> EoliaStatus:
    return EoliaStatus.from_dict(status_response)


@pytest.fixture
def new_status(control_response) -> EoliaStatus:
    return EoliaStatus.from_dict(control_response)


@pytest.fixture
def coordinator(hass, device) -> EoliaDataUpdateCoordinator:
    api = AsyncMock()
    return EoliaDataUpdateCoordinator(hass, api, [device])


@pytest.fixture
def initial_custom_settings(customsettings_response) -> EoliaCustomSettings:
    return EoliaCustomSettings.from_dict(customsettings_response)


async def test_set_status_payload_matches_fixed_contract(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    appliance_id_arg, payload = coordinator.api.async_set_status.call_args.args
    assert appliance_id_arg == APPLIANCE_ID
    assert "applianceId" not in payload
    assert "appliance_id" not in payload
    assert "humidity" not in payload
    assert "silence_control" in payload
    assert set(payload) == set(CONTROL_REQUEST_FIELDS)
    assert payload["operation_mode"] == "Cooling"


async def test_set_status_applies_only_requested_changes_on_top_of_last_known(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, wind_volume=4)

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["wind_volume"] == 4
    # Untouched fields carry over from the cached status, not defaults.
    assert payload["operation_mode"] == initial_status.operation_mode
    assert payload["nanoex"] == initial_status.nanoex


async def test_set_status_updates_cache_from_put_response_directly(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    assert coordinator.data[APPLIANCE_ID] is new_status


async def test_set_status_fetches_status_first_if_no_cached_data(
    coordinator, initial_status, new_status
):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    coordinator.api.async_get_status.assert_awaited_once_with(APPLIANCE_ID)


async def test_silence_control_has_no_readback_and_is_cached_locally(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    assert coordinator.get_silence_control(APPLIANCE_ID) is False

    await coordinator.async_set_status(APPLIANCE_ID, silence_control=True)
    assert coordinator.get_silence_control(APPLIANCE_ID) is True
    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["silence_control"] is True

    # An unrelated later write must still resend the cached silence_control value,
    # since the field is write-only and never comes back on a GET.
    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Heating")
    _, payload2 = coordinator.api.async_set_status.call_args.args
    assert payload2["silence_control"] is True


async def test_update_data_also_fetches_custom_settings(
    coordinator, initial_status, initial_custom_settings
):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_get_custom_settings.return_value = initial_custom_settings

    await coordinator._async_update_data()

    coordinator.api.async_get_custom_settings.assert_awaited_once_with(APPLIANCE_ID)
    assert coordinator.custom_settings[APPLIANCE_ID] is initial_custom_settings


async def test_custom_settings_fetch_failure_is_non_fatal(coordinator, initial_status):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_get_custom_settings.side_effect = EoliaApiError(400, "E-X", "boom")

    # Must not raise -- a customsettings failure shouldn't take down the whole poll.
    statuses = await coordinator._async_update_data()

    assert statuses[APPLIANCE_ID] is initial_status
    assert APPLIANCE_ID not in coordinator.custom_settings


async def test_set_custom_settings_payload_matches_fixed_contract(
    coordinator, initial_custom_settings
):
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    new_settings = EoliaCustomSettings.from_dict(
        {
            "double_mode_temp": {"status": True, "high": 28, "low": 22},
            "peak_cut": 100,
            "operation_priority": False,
            "device_errstatus": False,
            "operation_token": "TOKEN",
        }
    )
    coordinator.api.async_set_custom_settings.return_value = new_settings

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_low=22)

    appliance_id_arg, payload = coordinator.api.async_set_custom_settings.call_args.args
    assert appliance_id_arg == APPLIANCE_ID
    assert "appliance_id" not in payload
    assert set(payload) == set(CUSTOM_SETTINGS_REQUEST_FIELDS)
    assert payload["double_mode_temp"] == {"status": True, "high": 28, "low": 22}
    # Untouched fields (status, high) carry over from the cached settings.
    assert payload["peak_cut"] == initial_custom_settings.peak_cut


async def test_set_custom_settings_updates_cache_and_notifies_listeners(
    coordinator, initial_custom_settings
):
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    new_settings = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": False, "high": 28, "low": 23}, "peak_cut": 100}
    )
    coordinator.api.async_set_custom_settings.return_value = new_settings

    listener_calls = []
    remove_listener = coordinator.async_add_listener(lambda: listener_calls.append(1))
    try:
        await coordinator.async_set_custom_settings(
            APPLIANCE_ID, double_mode_temp_status=False
        )
        assert coordinator.custom_settings[APPLIANCE_ID] is new_settings
        assert listener_calls  # notified without waiting for the next poll
    finally:
        # async_add_listener() arms the coordinator's periodic-refresh timer as a side
        # effect; must unsubscribe or that timer lingers past the test and fails
        # pytest-homeassistant-custom-component's teardown check.
        remove_listener()


async def test_set_custom_settings_fetches_first_if_no_cached_data(
    coordinator, initial_custom_settings
):
    coordinator.api.async_get_custom_settings.return_value = initial_custom_settings
    coordinator.api.async_set_custom_settings.return_value = initial_custom_settings

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_high=27)

    coordinator.api.async_get_custom_settings.assert_awaited_once_with(APPLIANCE_ID)
