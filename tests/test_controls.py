"""Tests for controls.py: which controls apply in which state.

Every expectation traces to a live-confirmed rule in const.py / live_captures/39_fuzz_findings.md.
"""

from __future__ import annotations

import pytest

from custom_components.eolia import controls as c
from custom_components.eolia.const import CLEAN_FAMILY_MODES, EoliaOperationMode as Mode
from custom_components.eolia.controls import applicable_controls
from custom_components.eolia.models import EoliaStatus


@pytest.fixture
def make(status_response):
    def _make(mode: Mode | str = Mode.COOLING, **fields) -> set[str]:
        data = {
            **status_response,
            "operation_status": True,
            "operation_mode": str(mode),
            "air_flow": "not_set",
            "wind_shield_hit": "not_set",
            **fields,
        }
        return set(applicable_controls(EoliaStatus.from_dict(data)))

    return _make


def test_plain_cooling_offers_everything_but_mode_specific_controls(make):
    assert make(Mode.COOLING) == {
        c.TEMPERATURE, c.FAN_SPEED, c.VERTICAL_LOUVER, c.HORIZONTAL_LOUVER, c.AI_MODE, c.AIR_FLOW, c.WIND_SHIELD_HIT,
        c.NANOEX, c.SILENCE_CONTROL, c.AIR_QUALITY_MONITOR,
    }  # fmt: skip


# --- KeepMode: a /status dead end -----------------------------------------------------------
def test_keep_mode_offers_only_the_customsettings_controls(make):
    assert make(Mode.KEEP_MODE) == {c.DOUBLE_TEMP_LOW, c.DOUBLE_TEMP_HIGH}


def test_the_double_temp_on_off_switch_is_never_offered(make):
    # Picking / leaving the Double temperature mode is what turns it on and off; the card has
    # no separate switch for it, so it isn't a control in any state.
    for mode in Mode:
        assert "double_temp_enabled" not in make(mode)


def test_double_temp_range_only_exists_in_keep_mode(make):
    for mode in (Mode.AUTO, Mode.COOLING, Mode.COMFORTABLE_DEHUMIDIFICATION, Mode.BLAST):
        assert not {c.DOUBLE_TEMP_LOW, c.DOUBLE_TEMP_HIGH} & make(mode)


# --- Dry -------------------------------------------------------------------------------------
def test_dry_has_humidity_target_and_no_temperature_or_air_flow(make):
    controls = make(Mode.COMFORTABLE_DEHUMIDIFICATION)
    assert c.DRY_HUMIDITY_TARGET in controls
    assert c.TEMPERATURE not in controls
    assert c.AIR_FLOW not in controls  # dropped by the server in Dry
    assert {c.AI_MODE, c.FAN_SPEED, c.VERTICAL_LOUVER, c.HORIZONTAL_LOUVER, c.WIND_SHIELD_HIT} <= controls  # all honoured in Dry


def test_humidity_target_is_dry_only(make):
    for mode in (Mode.AUTO, Mode.COOLING, Mode.COOL_DEHUMIDIFYING, Mode.BLAST, Mode.CLOTHES_DRYER):
        assert c.DRY_HUMIDITY_TARGET not in make(mode)


# --- Blast / Nanoe / ClothesDryer -----------------------------------------------------------
@pytest.mark.parametrize("mode", [Mode.BLAST, Mode.NANOE, Mode.CLOTHES_DRYER])
def test_modes_that_drop_ai_air_flow_and_shield_hit(make, mode):
    controls = make(mode)
    assert not {c.AI_MODE, c.AIR_FLOW, c.WIND_SHIELD_HIT} & controls
    # fan, louvers and nanoeX are honoured in all of them
    assert {c.FAN_SPEED, c.VERTICAL_LOUVER, c.HORIZONTAL_LOUVER, c.NANOEX} <= controls


def test_clothes_dryer_has_no_temperature_but_blast_does(make):
    assert c.TEMPERATURE not in make(Mode.CLOTHES_DRYER)
    assert c.TEMPERATURE in make(Mode.BLAST)


# --- shield/hit and air_flow ----------------------------------------------------------------
@pytest.mark.parametrize("shield_hit", ["shield", "hit"])
def test_shield_hit_on_locks_fan_and_louvers(make, shield_hit):
    controls = make(Mode.COOLING, wind_shield_hit=shield_hit)
    assert not {c.FAN_SPEED, c.VERTICAL_LOUVER, c.HORIZONTAL_LOUVER} & controls
    # ...but the selects that turn it off / change it stay usable
    assert {c.WIND_SHIELD_HIT, c.AIR_FLOW} <= controls


@pytest.mark.parametrize("air_flow", ["quiet", "powerful", "long"])
def test_any_air_flow_value_locks_fan_speed_but_not_louvers(make, air_flow):
    controls = make(Mode.COOLING, air_flow=air_flow)
    assert c.FAN_SPEED not in controls
    assert {c.VERTICAL_LOUVER, c.HORIZONTAL_LOUVER} <= controls


# --- Off and the clean family ---------------------------------------------------------------
def test_off_offers_nothing_to_adjust(make):
    assert make(Mode.STOP, operation_status=False) == set()
    # a real mode carried while off is still "off"
    assert make(Mode.COOLING, operation_status=False) == set()


@pytest.mark.parametrize("mode", CLEAN_FAMILY_MODES)
def test_clean_family_offers_nothing_to_adjust(make, mode):
    # They run the unit while operation_status is False -- and even if it read True.
    assert make(mode, operation_status=False) == set()
    assert make(mode, operation_status=True) == set()


def test_unknown_mode_is_treated_like_an_ordinary_running_mode(make):
    assert c.FAN_SPEED in make("SomethingNew")


def test_result_has_no_duplicates(make, status_response):
    for mode in Mode:
        status = EoliaStatus.from_dict({**status_response, "operation_mode": str(mode)})
        result = applicable_controls(status)
        assert len(result) == len(set(result))
