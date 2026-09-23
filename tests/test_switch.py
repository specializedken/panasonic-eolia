"""Tests for switch.py's guard against changes while the AC is off.

Live-confirmed 2026-09-23 via the real HA integration: the API rejects any /status
field change (nanoex included, not just climate's fan/swing/temperature) while
operation_status is False, with a generic E-21291-01711. See climate.py's equivalent
guard and its test file for the first occurrence of this.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
from custom_components.eolia.models import EoliaDevice, EoliaStatus
from custom_components.eolia.switch import SWITCH_DESCRIPTIONS, EoliaSwitch

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
def coordinator(hass, device) -> EoliaDataUpdateCoordinator:
    return EoliaDataUpdateCoordinator(hass, AsyncMock(), [device])


def _status(*, operation_status: bool) -> EoliaStatus:
    return EoliaStatus.from_dict(
        {"appliance_id": APPLIANCE_ID, "operation_status": operation_status}
    )


@pytest.mark.parametrize("description", SWITCH_DESCRIPTIONS, ids=lambda d: d.key)
async def test_turn_on_raises_clear_error_when_off(coordinator, description):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=False)})
    entity = EoliaSwitch(coordinator, APPLIANCE_ID, description)

    with pytest.raises(HomeAssistantError, match="while the AC is off"):
        await entity.async_turn_on()

    coordinator.api.async_set_status.assert_not_awaited()


@pytest.mark.parametrize("description", SWITCH_DESCRIPTIONS, ids=lambda d: d.key)
async def test_turn_off_raises_clear_error_when_off(coordinator, description):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=False)})
    entity = EoliaSwitch(coordinator, APPLIANCE_ID, description)

    with pytest.raises(HomeAssistantError, match="while the AC is off"):
        await entity.async_turn_off()

    coordinator.api.async_set_status.assert_not_awaited()


@pytest.mark.parametrize("description", SWITCH_DESCRIPTIONS, ids=lambda d: d.key)
async def test_turn_on_proceeds_when_powered_on(coordinator, description):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=True)})
    coordinator.api.async_set_status.return_value = _status(operation_status=True)
    entity = EoliaSwitch(coordinator, APPLIANCE_ID, description)

    await entity.async_turn_on()

    coordinator.api.async_set_status.assert_awaited_once()


def test_airquality_switch_is_gated_by_the_models_airquality_flag(coordinator):
    # CS-712DX2-W reports airquality: false in /products/{code}/functions.
    airquality = next(d for d in SWITCH_DESCRIPTIONS if d.key == "airquality")
    assert airquality.function_id == "airquality"
    assert coordinator.supports(APPLIANCE_ID, airquality.function_id) is True
    coordinator.functions[APPLIANCE_ID] = {"airquality": False}
    assert coordinator.supports(APPLIANCE_ID, airquality.function_id) is False
    # Ungated switches (empty function_id) are never affected.
    nanoex = next(d for d in SWITCH_DESCRIPTIONS if d.key == "nanoex")
    assert coordinator.supports(APPLIANCE_ID, nanoex.function_id) is True
