"""Tests for climate.py's operation_mode <-> HVACMode mapping.

This is the "highest-value test" called out in PHASE1_PLAN.md's testing plan: every
operation_mode must land in a bucket (or be one of the two intentionally-excluded
values), and the hvac_mode -> default operation_mode table must round-trip back into
the same bucket it came from -- otherwise picking a coarse hvac_mode and reading it
back would silently show a different mode than what was actually set.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError

from custom_components.eolia.climate import (
    _DEFAULT_MODE_FOR_HVAC_MODE,
    _HVAC_MODE_BUCKETS,
    _SETTABLE_PRESET_MODES,
    _SWING_MODES,
    EoliaClimateEntity,
)
from custom_components.eolia.const import WIND_DIRECTION_SWING, EoliaOperationMode
from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
from custom_components.eolia.models import EoliaDevice, EoliaStatus

APPLIANCE_ID = "EXAMPLEAPPLIANCEID0000000000000000000000000="

@pytest.mark.parametrize("mode", list(EoliaOperationMode))
def test_every_operation_mode_is_bucketed_except_other(mode):
    # STOP has its own bucket (OFF); OTHER is the sole intentional fallback -- see
    # hvac_mode()'s ValueError/None handling in climate.py.
    if mode is EoliaOperationMode.OTHER:
        assert mode not in _HVAC_MODE_BUCKETS
    else:
        assert mode in _HVAC_MODE_BUCKETS, f"{mode} has no HVACMode bucket"


def test_stop_maps_to_off():
    assert _HVAC_MODE_BUCKETS[EoliaOperationMode.STOP] == HVACMode.OFF


@pytest.mark.parametrize(
    ("hvac_mode", "expected_operation_mode"),
    list(_DEFAULT_MODE_FOR_HVAC_MODE.items()),
)
def test_default_mode_for_hvac_mode_round_trips_into_its_own_bucket(
    hvac_mode, expected_operation_mode
):
    """Setting hvac_mode=X must pick a representative mode that reads back as X."""
    assert _HVAC_MODE_BUCKETS[expected_operation_mode] == hvac_mode


def test_stop_and_other_are_not_settable_presets():
    assert EoliaOperationMode.STOP.value not in _SETTABLE_PRESET_MODES
    assert EoliaOperationMode.OTHER.value not in _SETTABLE_PRESET_MODES


def test_plain_dehumidifying_is_not_a_settable_preset():
    # Confirmed rejected twice on this device (CLI and the real HA integration) --
    # excluded so it doesn't show as a pickable-but-broken option. The app's own
    # "dehumidification" menu item maps to ComfortableDehumidification instead.
    assert EoliaOperationMode.DEHUMIDIFYING.value not in _SETTABLE_PRESET_MODES
    # But it still has a bucket (for reading back state, if a different device ever
    # reports it) -- only removed from the settable list, not the whole model.
    assert EoliaOperationMode.DEHUMIDIFYING in _HVAC_MODE_BUCKETS


def test_keep_heating_is_not_a_settable_preset():
    # Live-confirmed 2026-09-23: rejected with E-21291-01711 (the same generic-error
    # code DEHUMIDIFYING hit) when deliberately selected from the real HA dropdown, with
    # an otherwise-valid payload -- not a real selectable mode on this device.
    assert EoliaOperationMode.KEEP_HEATING.value not in _SETTABLE_PRESET_MODES
    assert EoliaOperationMode.KEEP_HEATING in _HVAC_MODE_BUCKETS


def test_auto_temp_control_is_not_a_settable_preset():
    # Live-confirmed 2026-09-23 via the HA dropdown: a real 25.0 gets E-21291-01712 and
    # 0.0 gets E-21291-00007 -- no payload tried is accepted, so it's excluded.
    assert EoliaOperationMode.AUTO_TEMP_CONTROL.value not in _SETTABLE_PRESET_MODES
    assert EoliaOperationMode.AUTO_TEMP_CONTROL in _HVAC_MODE_BUCKETS


def test_nanoe_is_recognized_but_not_a_settable_preset():
    # Live-confirmed 2026-09-23: sending operation_mode=Blast with nanoex=True gets
    # silently substituted server-side for this instead (reproduced twice, independent
    # of ai_control) -- not directly reachable, so excluded from the settable list, but
    # must still have a bucket so it reads back as FAN_ONLY rather than the "unknown
    # mode" warning + OFF-bucket fallback.
    assert EoliaOperationMode.NANOE.value not in _SETTABLE_PRESET_MODES
    assert _HVAC_MODE_BUCKETS[EoliaOperationMode.NANOE] == HVACMode.FAN_ONLY


def test_every_hvac_mode_except_off_has_a_default_operation_mode():
    handled = set(_DEFAULT_MODE_FOR_HVAC_MODE) | {HVACMode.OFF}
    assert handled == {
        HVACMode.OFF,
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    }


def test_dry_vs_cool_and_dehumidify_are_distinct_preset_modes():
    """The whole point of this project -- these must never collapse to one value."""
    assert (
        EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION.value
        in _SETTABLE_PRESET_MODES
    )
    assert EoliaOperationMode.COOL_DEHUMIDIFYING.value in _SETTABLE_PRESET_MODES
    assert (
        EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION
        != EoliaOperationMode.COOL_DEHUMIDIFYING
    )
    # Dry vs Cool&Dehumidify land in different hvac_mode buckets too (DRY vs COOL).
    assert (
        _HVAC_MODE_BUCKETS[EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION]
        == HVACMode.DRY
    )
    assert (
        _HVAC_MODE_BUCKETS[EoliaOperationMode.COOL_DEHUMIDIFYING] == HVACMode.COOL
    )


def test_keepmode_maps_to_auto_bucket():
    """operation_mode=KeepMode is the app's 'double temperature setting' -- live
    confirmed 2026-09-23 (see tests/fixtures/live_captures/07)."""
    assert _HVAC_MODE_BUCKETS[EoliaOperationMode.KEEP_MODE] == HVACMode.AUTO


def test_swing_modes_include_the_confirmed_full_range():
    # wind_direction's full range live-confirmed 2026-09-23 (see
    # tests/fixtures/live_captures/15): 0=auto, 1-5=fixed positions, 6=swing.
    assert _SWING_MODES == ["0", "1", "2", "3", "4", "5", "6"]
    assert str(WIND_DIRECTION_SWING) in _SWING_MODES


# --- Guard against sub-setting changes while the AC is off -----------------------------
# Live-confirmed 2026-09-23 via the real HA integration: the server rejects
# temperature/fan/swing changes while operation_status is False with a generic,
# unhelpful E-21291-01711 ("an application error occurred"). preset_mode/hvac_mode
# already sidestep this by forcing operation_status=True alongside their own change;
# temperature/fan/swing have no mode of their own to piggyback that on, so climate.py
# raises a clear HomeAssistantError for them instead.

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


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("async_set_fan_mode", {"fan_mode": "3"}),
        ("async_set_swing_mode", {"swing_mode": "3"}),
        ("async_set_swing_horizontal_mode", {"swing_horizontal_mode": "front"}),
        ("async_set_temperature", {ATTR_TEMPERATURE: 24.0}),
    ],
)
async def test_sub_setting_changes_raise_clear_error_when_off(
    coordinator, method, kwargs
):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=False)})
    entity = EoliaClimateEntity(coordinator, APPLIANCE_ID)

    with pytest.raises(HomeAssistantError, match="while the AC is off"):
        await getattr(entity, method)(**kwargs)

    coordinator.api.async_set_status.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("async_set_fan_mode", {"fan_mode": "3"}),
        ("async_set_swing_mode", {"swing_mode": "3"}),
        ("async_set_swing_horizontal_mode", {"swing_horizontal_mode": "front"}),
        ("async_set_temperature", {ATTR_TEMPERATURE: 24.0}),
    ],
)
async def test_sub_setting_changes_proceed_when_on(coordinator, method, kwargs):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=True)})
    coordinator.api.async_set_status.return_value = _status(operation_status=True)
    entity = EoliaClimateEntity(coordinator, APPLIANCE_ID)

    await getattr(entity, method)(**kwargs)

    coordinator.api.async_set_status.assert_awaited_once()


# --- Guard against setting a temperature in modes that don't have one -------------------
# Live-confirmed 2026-09-23 via the real HA integration: dragging the climate card's
# temperature slider while in Dry mode (ComfortableDehumidification) returned a silent
# 200 OK with the temperature still forced to 0.0 by coordinator.py -- no error, no
# indication the request did nothing. climate.py now raises a clear error instead of
# letting that reach the coordinator.


def _status_in_mode(mode: EoliaOperationMode) -> EoliaStatus:
    return EoliaStatus.from_dict(
        {
            "appliance_id": APPLIANCE_ID,
            "operation_status": True,
            "operation_mode": mode.value,
        }
    )


@pytest.mark.parametrize(
    "mode",
    [EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION, EoliaOperationMode.CLOTHES_DRYER],
)
async def test_set_temperature_raises_clear_error_in_no_temperature_modes(
    coordinator, mode
):
    coordinator.async_set_updated_data({APPLIANCE_ID: _status_in_mode(mode)})
    entity = EoliaClimateEntity(coordinator, APPLIANCE_ID)

    with pytest.raises(HomeAssistantError, match="doesn't support a target temperature"):
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})

    coordinator.api.async_set_status.assert_not_awaited()


async def test_set_temperature_proceeds_in_a_normal_mode(coordinator):
    coordinator.async_set_updated_data(
        {APPLIANCE_ID: _status_in_mode(EoliaOperationMode.COOLING)}
    )
    coordinator.api.async_set_status.return_value = _status_in_mode(
        EoliaOperationMode.COOLING
    )
    entity = EoliaClimateEntity(coordinator, APPLIANCE_ID)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})

    coordinator.api.async_set_status.assert_awaited_once()


async def test_keep_mode_preset_is_routed_through_customsettings(coordinator):
    # Live-confirmed 2026-09-23: /status operation_mode=KeepMode is always rejected;
    # the mode is entered by enabling double_mode_temp.status on /customsettings.
    coordinator.async_set_updated_data({APPLIANCE_ID: _status(operation_status=False)})
    coordinator.async_set_custom_settings = AsyncMock()
    entity = EoliaClimateEntity(coordinator, APPLIANCE_ID)

    await entity.async_set_preset_mode(EoliaOperationMode.KEEP_MODE.value)

    coordinator.async_set_custom_settings.assert_awaited_once_with(
        APPLIANCE_ID, double_mode_temp_status=True
    )
    coordinator.api.async_set_status.assert_not_awaited()
