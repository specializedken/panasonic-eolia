"""Tests for number.py's entity descriptions.

Bounds are the app-enforced ranges found statically (see docs/findings.md); the actual
low/high values themselves are live-confirmed against the app 2026-09-23.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.eolia.const import (
    DOUBLE_MODE_TEMP_HIGH_RANGE,
    DOUBLE_MODE_TEMP_LOW_RANGE,
    DRY_MODE_HUMIDITY_RANGE,
    DRY_MODE_HUMIDITY_STEP,
)
from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
from custom_components.eolia.models import EoliaCustomSettings, EoliaDevice
from custom_components.eolia.number import NUMBER_DESCRIPTIONS, EoliaDryHumidityNumber

APPLIANCE_ID = "EXAMPLEAPPLIANCEID0000000000000000000000000="


def _description(key: str):
    return next(d for d in NUMBER_DESCRIPTIONS if d.key == key)


def test_double_temp_low_bounds_match_const():
    description = _description("double_temp_low")
    assert description.native_min_value == DOUBLE_MODE_TEMP_LOW_RANGE[0]
    assert description.native_max_value == DOUBLE_MODE_TEMP_LOW_RANGE[1]
    assert description.control_field == "double_mode_temp_low"


def test_double_temp_high_bounds_match_const():
    description = _description("double_temp_high")
    assert description.native_min_value == DOUBLE_MODE_TEMP_HIGH_RANGE[0]
    assert description.native_max_value == DOUBLE_MODE_TEMP_HIGH_RANGE[1]
    assert description.control_field == "double_mode_temp_high"


def test_value_fns_read_the_right_double_mode_temp_field(customsettings_response):
    settings = EoliaCustomSettings.from_dict(customsettings_response)
    assert _description("double_temp_low").value_fn(settings) == settings.double_mode_temp.low
    assert (
        _description("double_temp_high").value_fn(settings) == settings.double_mode_temp.high
    )


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


def test_dry_humidity_number_bounds_match_const(coordinator):
    # Deliberately restrictive (only 50/55/60 valid, not a free 0-100% range) -- see
    # coordinator.py/const.py's DRY_MODE_HUMIDITY_RANGE docstring for why this needed a
    # dedicated number entity instead of ClimateEntity's native target_humidity.
    # Checked via an instance, not the class -- HA's NumberEntity implements _attr_*
    # as class-level properties (for its cached_property optimization), so class-level
    # access returns the descriptor itself rather than the assigned value.
    entity = EoliaDryHumidityNumber(coordinator, APPLIANCE_ID)
    assert entity.native_min_value == DRY_MODE_HUMIDITY_RANGE[0]
    assert entity.native_max_value == DRY_MODE_HUMIDITY_RANGE[1]
    assert entity.native_step == DRY_MODE_HUMIDITY_STEP


def test_dry_humidity_number_reads_from_coordinator_cache(coordinator):
    entity = EoliaDryHumidityNumber(coordinator, APPLIANCE_ID)
    assert entity.native_value == coordinator.get_humidity(APPLIANCE_ID) == 50
