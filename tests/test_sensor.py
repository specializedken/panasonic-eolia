"""Tests for sensor.py's entity descriptions."""

from __future__ import annotations

import pytest

from custom_components.eolia.const import EoliaOperationMode, operation_mode_key
from custom_components.eolia.models import EoliaStatus
from custom_components.eolia.sensor import SENSOR_DESCRIPTIONS


def _description(key: str):
    return next(d for d in SENSOR_DESCRIPTIONS if d.key == key)


def test_operation_mode_options_match_enum():
    assert _description("operation_mode").options == [
        operation_mode_key(mode.value) for mode in EoliaOperationMode
    ]


@pytest.mark.parametrize("mode", list(EoliaOperationMode))
def test_operation_mode_reports_every_known_mode_as_its_key(status_response, mode):
    status = EoliaStatus.from_dict({**status_response, "operation_mode": mode.value})
    assert _description("operation_mode").value_fn(status) == operation_mode_key(mode.value)


def test_operation_mode_folds_unknown_values_into_other(status_response):
    # An ENUM sensor raises on a state outside `options`; the server can send modes
    # this integration has never seen.
    status = EoliaStatus.from_dict(
        {**status_response, "operation_mode": "SomethingNew"}
    )
    assert _description("operation_mode").value_fn(status) == "other"


def test_operation_mode_sensor_exposes_applicable_controls(status_response):
    from custom_components.eolia.controls import applicable_controls

    status = EoliaStatus.from_dict(
        {**status_response, "operation_status": True, "operation_mode": "KeepMode"}
    )
    attrs = _description("operation_mode").attrs_fn(status)
    assert attrs["controls"] == list(applicable_controls(status))
    assert "double_temp_low" in attrs["controls"]


def test_only_the_operation_mode_sensor_has_attributes():
    assert [d.key for d in SENSOR_DESCRIPTIONS if d.attrs_fn] == ["operation_mode"]


def test_mode_tooltips_cover_every_pickable_mode_in_plain_english():
    import re

    from custom_components.eolia.const import OPERATION_MODE_TOOLTIPS

    pickable = {m.value for m in EoliaOperationMode} - {"Stop", "Other"}
    assert set(OPERATION_MODE_TOOLTIPS) == pickable
    for mode, text in OPERATION_MODE_TOOLTIPS.items():
        assert text.strip(), mode
        # user-facing copy: no error codes, no Japanese labels, no developer jargon
        assert not re.search(r"E-\d{5}|[　-鿿]|API|endpoint", text), (mode, text)


def test_operation_mode_sensor_exposes_the_tooltips_but_does_not_record_them(status_response):
    from custom_components.eolia.const import OPERATION_MODE_TOOLTIPS
    from custom_components.eolia.sensor import EoliaSensor

    attrs = _description("operation_mode").attrs_fn(EoliaStatus.from_dict(status_response))
    assert attrs["mode_descriptions"] == {
        operation_mode_key(mode): text for mode, text in OPERATION_MODE_TOOLTIPS.items()
    }
    assert "mode_descriptions" in EoliaSensor._unrecorded_attributes


def test_operation_mode_sensor_exposes_the_double_temp_min_gap(status_response):
    from custom_components.eolia.const import DOUBLE_MODE_TEMP_MIN_GAP

    attrs = _description("operation_mode").attrs_fn(EoliaStatus.from_dict(status_response))
    # The card enforces the low/high gap instantly from this; it must not hardcode the number.
    assert attrs["double_temp_min_gap"] == DOUBLE_MODE_TEMP_MIN_GAP == 5


# --- Air-quality sensors exist only on models with the `airquality` capability -----------------
def _setup_sensors(hass, functions):
    """Run the platform's setup against a real coordinator; return the created entities."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
    from custom_components.eolia.models import EoliaDevice
    from custom_components.eolia.sensor import async_setup_entry

    appliance_id = "EXAMPLEAPPLIANCEID0000000000000000000000000="
    coordinator = EoliaDataUpdateCoordinator(
        hass,
        AsyncMock(),
        [EoliaDevice(appliance_id=appliance_id, nickname="Yurt", product_code="CS-712DX2-W", product_name="T")],
    )
    if functions is not None:
        coordinator.functions[appliance_id] = functions
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    added = []

    async def run():
        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    return run, added


async def test_air_quality_sensors_are_not_created_on_a_model_without_the_feature(hass):
    run, added = _setup_sensors(hass, {"airquality": False})
    await run()
    keys = {e.entity_description.key for e in added}
    assert "aq_name" not in keys and "aq_value" not in keys
    assert {"inside_temp", "outside_temp", "inside_humidity", "operation_mode"} <= keys


async def test_air_quality_sensors_are_created_on_a_model_with_the_feature(hass):
    run, added = _setup_sensors(hass, {"airquality": True})
    await run()
    assert {"aq_name", "aq_value"} <= {e.entity_description.key for e in added}


async def test_air_quality_sensors_are_created_when_capabilities_are_unknown(hass):
    # A failed /functions fetch must not make sensors disappear.
    run, added = _setup_sensors(hass, None)
    await run()
    assert {"aq_name", "aq_value"} <= {e.entity_description.key for e in added}


def test_only_the_air_quality_sensors_are_capability_gated():
    gated = {d.key: d.function_id for d in SENSOR_DESCRIPTIONS if d.function_id}
    assert gated == {"aq_name": "airquality", "aq_value": "airquality"}
