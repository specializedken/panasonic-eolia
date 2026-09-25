"""Regression tests for models.py's control-request field contract.

This directly encodes the finding that blocked live PUT testing for a day (see
docs/findings.md's "RESOLVED" section): applianceId must never appear in the PUT body
(URL only) and humidity must be excluded even though it's present on every GET
response.
"""

from __future__ import annotations

from custom_components.eolia.const import CONTROL_REQUEST_FIELDS, CUSTOM_SETTINGS_REQUEST_FIELDS
from custom_components.eolia.models import (
    EoliaCustomSettings,
    EoliaDevice,
    EoliaDoubleModeTemp,
    EoliaStatus,
)


def test_status_from_dict_reads_real_capture(status_response):
    status = EoliaStatus.from_dict(status_response)
    assert status.appliance_id == status_response["appliance_id"]
    assert status.operation_mode == "ComfortableDehumidification"
    assert status.ai_control == "off"
    assert status.nanoex is True
    assert status.inside_temp == 22.5
    assert status.outside_temp == 23.0


def test_status_from_dict_defaults_missing_optional_fields():
    status = EoliaStatus.from_dict({"appliance_id": "X"})
    assert status.operation_mode == "Stop"
    assert status.operation_status is False
    assert status.inside_temp is None
    assert status.outside_temp is None
    assert status.aq_name is None
    assert status.operation_token is None


def test_outside_temp_sentinel_becomes_none():
    # Live-confirmed repeatedly (right after power-on, and again on power-off): the API
    # sends 999.0 as a "no reading available" sentinel instead of omitting the field or
    # sending null. Must never surface as a literal 999C sensor reading in HA.
    status = EoliaStatus.from_dict({"appliance_id": "X", "outside_temp": 999.0})
    assert status.outside_temp is None


def test_outside_temp_real_value_passes_through():
    status = EoliaStatus.from_dict({"appliance_id": "X", "outside_temp": 23.0})
    assert status.outside_temp == 23.0


def test_to_control_fields_excludes_applianceid_and_humidity(status_response):
    # The real GET response has a `humidity` field -- to_control_fields() must never
    # re-emit it, and must never emit appliance_id (URL-only) or silence_control
    # (injected by the coordinator, not part of this model).
    assert "humidity" in status_response  # sanity: the source data really has it
    status = EoliaStatus.from_dict(status_response)
    fields = status.to_control_fields()
    assert "humidity" not in fields
    assert "appliance_id" not in fields
    assert "applianceId" not in fields
    assert "silence_control" not in fields


def test_to_control_fields_matches_fixed_contract(status_response):
    status = EoliaStatus.from_dict(status_response)
    fields = status.to_control_fields()
    assert set(fields) == set(CONTROL_REQUEST_FIELDS) - {"silence_control"}


def test_device_from_dict_real_capture(devices_response):
    device = EoliaDevice.from_dict(devices_response["ac_list"][0])
    assert device.appliance_id == "EXAMPLEAPPLIANCEID0000000000000000000000000="
    assert device.nickname == "Yurt"
    assert device.product_code == "CS-712DX2-W"


def test_device_from_dict_falls_back_to_product_code_when_nickname_missing():
    device = EoliaDevice.from_dict(
        {"appliance_id": "X", "product_code": "CS-712DX2-W"}
    )
    assert device.nickname == "CS-712DX2-W"


def test_double_mode_temp_from_dict_reads_real_capture(customsettings_response):
    dmt = EoliaDoubleModeTemp.from_dict(customsettings_response["double_mode_temp"])
    assert dmt.status is True
    assert dmt.high == 28
    assert dmt.low == 23


def test_double_mode_temp_to_dict_round_trips():
    dmt = EoliaDoubleModeTemp(status=True, high=28, low=22)
    assert dmt.to_dict() == {"status": True, "high": 28, "low": 22}


def test_custom_settings_from_dict_reads_real_capture(customsettings_response):
    # Real GET response has no appliance_id field at all, unlike /status.
    assert "appliance_id" not in customsettings_response
    settings = EoliaCustomSettings.from_dict(customsettings_response)
    assert settings.double_mode_temp.high == 28
    assert settings.double_mode_temp.low == 23
    assert settings.peak_cut == 100
    assert settings.operation_priority is False
    assert settings.device_errstatus is False
    # operation_token only shows up on PUT responses, not GET -- confirmed live.
    assert settings.operation_token is None


def test_custom_settings_to_control_fields_excludes_appliance_id(customsettings_response):
    settings = EoliaCustomSettings.from_dict(customsettings_response)
    fields = settings.to_control_fields()
    assert "appliance_id" not in fields
    assert "applianceId" not in fields
    assert "operation_token" not in fields
    assert set(fields) == set(CUSTOM_SETTINGS_REQUEST_FIELDS)
    assert fields["double_mode_temp"] == {"status": True, "high": 28, "low": 23}
    assert fields["peak_cut"] == 100
