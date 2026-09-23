"""Tests for select.py's entity descriptions.

Options for air_flow and wind_shield_hit are all live-confirmed against the app
2026-09-23 -- see tests/fixtures/live_captures/10-14.
"""

from __future__ import annotations

from custom_components.eolia.const import EoliaAiControl, EoliaAirFlow, EoliaWindShieldHit
from custom_components.eolia.select import SELECT_DESCRIPTIONS


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
    from custom_components.eolia.models import EoliaStatus

    status = EoliaStatus.from_dict(status_response)
    assert _description("ai_mode").current_option_fn(status) == status.ai_control
    assert _description("air_flow").current_option_fn(status) == status.air_flow
    assert (
        _description("wind_shield_hit").current_option_fn(status)
        == status.wind_shield_hit
    )
