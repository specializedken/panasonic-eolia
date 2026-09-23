"""Tests for select.py's entity descriptions.

Options for air_flow and wind_shield_hit are all live-confirmed against the app
2026-09-23 -- see tests/fixtures/live_captures/10-14.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eolia.const import EoliaAiControl, EoliaAirFlow, EoliaWindShieldHit
from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
from custom_components.eolia.models import EoliaDevice, EoliaStatus
from custom_components.eolia.select import SELECT_DESCRIPTIONS, EoliaSelect

APPLIANCE_ID = "EXAMPLEAPPLIANCEID0000000000000000000000000="


def _description(key: str):
    return next(d for d in SELECT_DESCRIPTIONS if d.key == key)


def test_ai_mode_options_match_enum():
    assert _description("ai_mode").options == [mode.value for mode in EoliaAiControl]
    assert _description("ai_mode").control_field == "ai_control"


def test_air_flow_options_match_enum():
    assert _description("air_flow").options == [mode.value for mode in EoliaAirFlow]
    assert _description("air_flow").control_field == "air_flow"


def test_wind_shield_hit_options_match_enum():
    assert _description("wind_shield_hit").options == [
        mode.value for mode in EoliaWindShieldHit
    ]
    assert _description("wind_shield_hit").control_field == "wind_shield_hit"


def test_current_option_fn_reads_the_right_status_field(status_response):
    status = EoliaStatus.from_dict(status_response)
    assert _description("ai_mode").current_option_fn(status) == status.ai_control
    assert _description("air_flow").current_option_fn(status) == status.air_flow
    assert (
        _description("wind_shield_hit").current_option_fn(status)
        == status.wind_shield_hit
    )


# --- Guard against changes while the AC is off ------------------------------------------
# Live-confirmed 2026-09-23 via the real HA integration: changing ai_control while off
# hit the same generic E-21291-01711 climate.py/switch.py's setters were already guarded
# against. See entity.py's shared _require_powered_on().

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


@pytest.mark.parametrize("description", SELECT_DESCRIPTIONS, ids=lambda d: d.key)
async def test_select_option_raises_clear_error_when_off(coordinator, description):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=False)})
    entity = EoliaSelect(coordinator, APPLIANCE_ID, description)

    with pytest.raises(HomeAssistantError, match="while the AC is off"):
        await entity.async_select_option(description.options[0])

    coordinator.api.async_set_status.assert_not_awaited()


@pytest.mark.parametrize("description", SELECT_DESCRIPTIONS, ids=lambda d: d.key)
async def test_select_option_proceeds_when_powered_on(coordinator, description):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=True)})
    coordinator.api.async_set_status.return_value = _status(operation_status=True)
    entity = EoliaSelect(coordinator, APPLIANCE_ID, description)

    await entity.async_select_option(description.options[0])

    coordinator.api.async_set_status.assert_awaited_once()
