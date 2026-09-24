"""Tests for sensor.py's entity descriptions."""

from __future__ import annotations

import pytest

from custom_components.eolia.const import EoliaOperationMode
from custom_components.eolia.models import EoliaStatus
from custom_components.eolia.sensor import SENSOR_DESCRIPTIONS


def _description(key: str):
    return next(d for d in SENSOR_DESCRIPTIONS if d.key == key)


def test_operation_mode_options_match_enum():
    assert _description("operation_mode").options == [
        mode.value for mode in EoliaOperationMode
    ]


@pytest.mark.parametrize("mode", list(EoliaOperationMode))
def test_operation_mode_reports_every_known_mode_verbatim(status_response, mode):
    status = EoliaStatus.from_dict({**status_response, "operation_mode": mode.value})
    assert _description("operation_mode").value_fn(status) == mode.value


def test_operation_mode_folds_unknown_values_into_other(status_response):
    # An ENUM sensor raises on a state outside `options`; the server can send modes
    # this integration has never seen.
    status = EoliaStatus.from_dict(
        {**status_response, "operation_mode": "SomethingNew"}
    )
    assert _description("operation_mode").value_fn(status) == "Other"


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
    assert attrs["mode_descriptions"] == OPERATION_MODE_TOOLTIPS
    assert "mode_descriptions" in EoliaSensor._unrecorded_attributes
