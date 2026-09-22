"""Tests for climate.py's operation_mode <-> HVACMode mapping.

This is the "highest-value test" called out in PHASE1_PLAN.md's testing plan: every
operation_mode must land in a bucket (or be one of the two intentionally-excluded
values), and the hvac_mode -> default operation_mode table must round-trip back into
the same bucket it came from -- otherwise picking a coarse hvac_mode and reading it
back would silently show a different mode than what was actually set.
"""

from __future__ import annotations

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.eolia.climate import (
    _DEFAULT_MODE_FOR_HVAC_MODE,
    _HVAC_MODE_BUCKETS,
    _SETTABLE_PRESET_MODES,
)
from custom_components.eolia.const import EoliaOperationMode

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
