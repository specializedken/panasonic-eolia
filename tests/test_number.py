"""Tests for number.py's entity descriptions.

Bounds are the app-enforced ranges found statically (see findings.md); the actual
low/high values themselves are live-confirmed against the app 2026-09-23.
"""

from __future__ import annotations

from custom_components.eolia.const import DOUBLE_MODE_TEMP_HIGH_RANGE, DOUBLE_MODE_TEMP_LOW_RANGE
from custom_components.eolia.models import EoliaCustomSettings
from custom_components.eolia.number import NUMBER_DESCRIPTIONS


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
