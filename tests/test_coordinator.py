"""Tests for coordinator.py's read-modify-write PUT-body contract.

async_set_status() is the only place a PUT body gets built. These tests regression-lock
the exact finding that blocked live testing for a day (see docs/findings.md's "RESOLVED"
section): the outgoing payload must never contain applianceId/humidity and must always
contain silence_control, even though silence_control has no GET readback at all.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eolia.const import CONTROL_REQUEST_FIELDS, CUSTOM_SETTINGS_REQUEST_FIELDS
from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
from custom_components.eolia.exceptions import EoliaApiError, EoliaDeviceLockedError
from custom_components.eolia.models import EoliaCustomSettings, EoliaDevice, EoliaStatus

APPLIANCE_ID = "EXAMPLEAPPLIANCEID0000000000000000000000000="


def _status_with_mode(base: EoliaStatus, operation_mode: str) -> EoliaStatus:
    """A copy of `base` with operation_mode overridden -- keeps mocked API responses
    consistent with what was actually requested, since async_set_status now checks for a
    mismatch (see the "Silent operation_mode substitution/rejection" tests below)."""
    return EoliaStatus.from_dict(
        {
            **base.to_control_fields(),
            "appliance_id": base.appliance_id,
            "operation_mode": operation_mode,
        }
    )


@pytest.fixture(autouse=True)
def _no_resync_delay(monkeypatch):
    """The KeepMode status re-read retries with a real 2 s sleep; tests must not wait."""
    monkeypatch.setattr("custom_components.eolia.coordinator._RESYNC_DELAY_SECONDS", 0)


@pytest.fixture
def device() -> EoliaDevice:
    return EoliaDevice(
        appliance_id=APPLIANCE_ID,
        nickname="Yurt",
        product_code="CS-712DX2-W",
        product_name="Test",
    )


@pytest.fixture
def initial_status(status_response) -> EoliaStatus:
    return EoliaStatus.from_dict(status_response)


@pytest.fixture
def new_status(control_response) -> EoliaStatus:
    return EoliaStatus.from_dict(control_response)


@pytest.fixture
def coordinator(hass, device) -> EoliaDataUpdateCoordinator:
    api = AsyncMock()
    return EoliaDataUpdateCoordinator(hass, api, [device])


@pytest.fixture
def initial_custom_settings(customsettings_response) -> EoliaCustomSettings:
    return EoliaCustomSettings.from_dict(customsettings_response)


async def test_set_status_payload_matches_fixed_contract(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    appliance_id_arg, payload = coordinator.api.async_set_status.call_args.args
    assert appliance_id_arg == APPLIANCE_ID
    assert "applianceId" not in payload
    assert "appliance_id" not in payload
    assert "humidity" not in payload
    assert "silence_control" in payload
    assert set(payload) == set(CONTROL_REQUEST_FIELDS)
    assert payload["operation_mode"] == "Cooling"


async def test_set_status_applies_only_requested_changes_on_top_of_last_known(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    # The server echoes the change back (a mock that doesn't would look like an ignored one).
    coordinator.api.async_set_status.return_value = EoliaStatus.from_dict(
        {
            **new_status.to_control_fields(),
            "appliance_id": APPLIANCE_ID,
            "wind_volume": 4,
        }
    )

    await coordinator.async_set_status(APPLIANCE_ID, wind_volume=4)

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["wind_volume"] == 4
    # Untouched fields carry over from the cached status, not defaults.
    assert payload["operation_mode"] == initial_status.operation_mode
    assert payload["nanoex"] == initial_status.nanoex


async def test_set_status_updates_cache_from_put_response_directly(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    assert coordinator.data[APPLIANCE_ID] is new_status


async def test_set_status_fetches_status_first_if_no_cached_data(
    coordinator, initial_status, new_status
):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    coordinator.api.async_get_status.assert_awaited_once_with(APPLIANCE_ID)


async def test_silence_control_has_no_readback_and_is_cached_locally(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    assert coordinator.get_silence_control(APPLIANCE_ID) is False

    await coordinator.async_set_status(APPLIANCE_ID, silence_control=True)
    assert coordinator.get_silence_control(APPLIANCE_ID) is True
    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["silence_control"] is True

    # An unrelated later write must still resend the cached silence_control value,
    # since the field is write-only and never comes back on a GET.
    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")
    _, payload2 = coordinator.api.async_set_status.call_args.args
    assert payload2["silence_control"] is True


async def test_update_data_also_fetches_custom_settings(
    coordinator, initial_status, initial_custom_settings
):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_get_custom_settings.return_value = initial_custom_settings

    await coordinator._async_update_data()

    coordinator.api.async_get_custom_settings.assert_awaited_once_with(APPLIANCE_ID)
    assert coordinator.custom_settings[APPLIANCE_ID] is initial_custom_settings


async def test_update_data_remembers_a_real_temperature_from_a_poll(
    coordinator, new_status
):
    # new_status (control_response fixture) is Cooling @ 20.0 -- a real, nonzero temp.
    coordinator.api.async_get_status.return_value = new_status
    coordinator.api.async_get_custom_settings.return_value = AsyncMock()

    await coordinator._async_update_data()

    assert coordinator._temperature_cache[APPLIANCE_ID] == 20.0


async def test_custom_settings_fetch_failure_is_non_fatal(coordinator, initial_status):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_get_custom_settings.side_effect = EoliaApiError(400, "E-X", "boom")

    # Must not raise -- a customsettings failure shouldn't take down the whole poll.
    statuses = await coordinator._async_update_data()

    assert statuses[APPLIANCE_ID] is initial_status
    assert APPLIANCE_ID not in coordinator.custom_settings


async def test_set_custom_settings_payload_matches_fixed_contract(
    coordinator, initial_custom_settings
):
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    new_settings = EoliaCustomSettings.from_dict(
        {
            "double_mode_temp": {"status": True, "high": 28, "low": 22},
            "peak_cut": 100,
            "operation_priority": False,
            "device_errstatus": False,
            "operation_token": "TOKEN",
        }
    )
    coordinator.api.async_set_custom_settings.return_value = new_settings

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_low=22)

    appliance_id_arg, payload = coordinator.api.async_set_custom_settings.call_args.args
    assert appliance_id_arg == APPLIANCE_ID
    assert "appliance_id" not in payload
    assert set(payload) == set(CUSTOM_SETTINGS_REQUEST_FIELDS)
    assert payload["double_mode_temp"] == {"status": True, "high": 28, "low": 22}
    # Untouched fields (status, high) carry over from the cached settings.
    assert payload["peak_cut"] == initial_custom_settings.peak_cut


async def test_set_custom_settings_updates_cache_and_notifies_listeners(
    coordinator, initial_custom_settings
):
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    new_settings = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": False, "high": 28, "low": 23}, "peak_cut": 100}
    )
    coordinator.api.async_set_custom_settings.return_value = new_settings

    listener_calls = []
    remove_listener = coordinator.async_add_listener(lambda: listener_calls.append(1))
    try:
        await coordinator.async_set_custom_settings(
            APPLIANCE_ID, double_mode_temp_status=False
        )
        assert coordinator.custom_settings[APPLIANCE_ID] is new_settings
        assert listener_calls  # notified without waiting for the next poll
    finally:
        # async_add_listener() arms the coordinator's periodic-refresh timer as a side
        # effect; must unsubscribe or that timer lingers past the test and fails
        # pytest-homeassistant-custom-component's teardown check.
        remove_listener()


async def test_set_custom_settings_fetches_first_if_no_cached_data(
    coordinator, initial_custom_settings
):
    coordinator.api.async_get_custom_settings.return_value = initial_custom_settings
    # Response must actually reflect the requested change (high=27), or the new
    # request-vs-response mismatch check below fires -- see
    # test_set_custom_settings_raises_when_server_silently_ignores_the_change.
    new_settings = EoliaCustomSettings.from_dict(
        {
            # high=27 leaves only a 4 degree gap to low=23, so low is nudged to 22.
            "double_mode_temp": {"status": True, "high": 27, "low": 22},
            "peak_cut": initial_custom_settings.peak_cut,
        }
    )
    coordinator.api.async_set_custom_settings.return_value = new_settings

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_high=27)

    coordinator.api.async_get_custom_settings.assert_awaited_once_with(APPLIANCE_ID)


async def test_set_custom_settings_raises_when_server_silently_ignores_the_change(
    coordinator, initial_custom_settings
):
    # Live-confirmed 2026-09-23: the server can return 200 OK while silently NOT
    # applying part of the request (e.g. accepting status=True but keeping it False
    # when high/low are still 0/0) -- no error code at all.
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    unchanged_settings = EoliaCustomSettings.from_dict(
        {
            "double_mode_temp": {"status": False, "high": 0, "low": 0},
            "peak_cut": 100,
        }
    )
    coordinator.api.async_set_custom_settings.return_value = unchanged_settings

    with pytest.raises(HomeAssistantError, match="didn't apply it as asked"):
        await coordinator.async_set_custom_settings(
            APPLIANCE_ID, double_mode_temp_status=True
        )

    # The actual (unchanged) truth must still be cached, so is_on etc. stay correct.
    assert coordinator.custom_settings[APPLIANCE_ID] is unchanged_settings


# --- Dry mode (ComfortableDehumidification) humidity handling --------------------------
# Live-confirmed 2026-09-23 (tests/fixtures/live_captures/19-24): this mode requires
# `humidity` in the payload and rejects any nonzero `temperature` -- the one exception to
# the general contract.

async def test_switching_to_dry_mode_forces_temp_zero_and_includes_humidity(
    coordinator, new_status
):
    # new_status (control_response fixture) is Cooling @ 20.0 -- a real, nonzero temp.
    coordinator.async_set_updated_data({APPLIANCE_ID: new_status})
    coordinator.api.async_set_status.return_value = _status_with_mode(
        new_status, "ComfortableDehumidification"
    )

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_mode="ComfortableDehumidification"
    )

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["operation_mode"] == "ComfortableDehumidification"
    assert payload["temperature"] == 0.0
    assert payload["humidity"] == 50  # default when nothing cached yet


async def test_dry_mode_humidity_write_is_cached_and_sent(coordinator, new_status):
    coordinator.async_set_updated_data({APPLIANCE_ID: new_status})
    coordinator.api.async_set_status.return_value = _status_with_mode(
        new_status, "ComfortableDehumidification"
    )

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_mode="ComfortableDehumidification", humidity=60
    )

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["humidity"] == 60
    assert coordinator.get_humidity(APPLIANCE_ID) == 60


async def test_staying_in_dry_mode_resends_cached_humidity(coordinator, initial_status):
    # initial_status (status_response fixture) is already ComfortableDehumidification.
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    # The real server echoes the stored target back.
    coordinator.api.async_set_status.return_value = EoliaStatus.from_dict(
        {**_dry_body(initial_status), "humidity": 55}
    )
    await coordinator.async_set_status(
        APPLIANCE_ID, operation_mode="ComfortableDehumidification", humidity=55
    )

    # An unrelated later write (still in Dry mode) must still resend the cached humidity.
    await coordinator.async_set_status(APPLIANCE_ID, nanoex=True)

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["operation_mode"] == "ComfortableDehumidification"
    assert payload["humidity"] == 55
    assert payload["temperature"] == 0.0


async def test_humidity_excluded_when_target_mode_is_not_dry(coordinator, initial_status):
    # initial_status is ComfortableDehumidification -- switching away from it.
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = _status_with_mode(
        initial_status, "Cooling"
    )

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["operation_mode"] == "Cooling"
    assert "humidity" not in payload


# --- Silent operation_mode substitution/rejection ---------------------------------------
# A requested operation_mode can come back different with a 200 and no error code -- same
# "server accepts but doesn't apply" class of bug as the double_mode_temp mismatch check above.
# (MoistCooling was once seen downgrading to Cooling, but did not reproduce on 2026-09-24;
# the tests just use it as a convenient example of a requested-vs-returned mismatch.)


async def test_operation_mode_mismatch_raises_clear_error_and_still_caches_truth(
    coordinator, initial_status, new_status
):
    # new_status (control_response fixture) reports Cooling -- simulates the real
    # MoistCooling -> Cooling downgrade.
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    with pytest.raises(HomeAssistantError, match="applied a different mode than asked"):
        await coordinator.async_set_status(APPLIANCE_ID, operation_mode="MoistCooling")

    # The actual (different) truth must still be cached, same as the double_mode_temp
    # mismatch case -- so the climate entity reflects reality, not the failed request.
    assert coordinator.data[APPLIANCE_ID] is new_status


async def test_operation_mode_mismatch_not_raised_for_the_known_blast_nanoe_combo(
    coordinator, initial_status
):
    nanoe_status = _status_with_mode(initial_status, "Nanoe")
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = nanoe_status

    # Must not raise -- Blast + nanoex=True legitimately becomes Nanoe on this device.
    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Blast", nanoex=True)

    assert coordinator.data[APPLIANCE_ID] is nanoe_status


async def test_operation_mode_mismatch_not_checked_when_mode_wasnt_explicitly_requested(
    coordinator, initial_status
):
    # An incidental mode substitution as a side effect of some other field change (e.g.
    # toggling nanoex while already in Blast) must not be flagged -- only an explicit,
    # caller-requested operation_mode change is checked.
    nanoe_status = _status_with_mode(initial_status, "Nanoe")
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = nanoe_status

    await coordinator.async_set_status(APPLIANCE_ID, nanoex=True)

    assert coordinator.data[APPLIANCE_ID] is nanoe_status


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"double_mode_temp_low": 16}, {"high": 21, "low": 16}),
        ({"double_mode_temp_low": 22}, {"high": 27, "low": 22}),
        ({"double_mode_temp_low": 25}, {"high": 30, "low": 25}),
        ({"double_mode_temp_high": 28}, {"high": 28, "low": 23}),
        ({"double_mode_temp_high": 21}, {"high": 21, "low": 16}),
    ],
)
async def test_setting_one_double_temp_bound_fills_the_unset_other_bound(
    coordinator, changes, expected
):
    # Live-confirmed 2026-09-23: the range resets to 0/0 outside KeepMode, and sending
    # e.g. high=0/low=16 was rejected with E-21291-02006.
    unset = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": False, "high": 0, "low": 0}, "peak_cut": 100}
    )
    coordinator.custom_settings[APPLIANCE_ID] = unset
    coordinator.api.async_set_custom_settings.return_value = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": False, **expected}, "peak_cut": 100}
    )

    await coordinator.async_set_custom_settings(APPLIANCE_ID, **changes)

    _, payload = coordinator.api.async_set_custom_settings.call_args.args
    assert payload["double_mode_temp"] == {"status": False, **expected}


async def test_turning_double_temp_on_with_no_range_sends_a_default_range(coordinator):
    # Live-confirmed 2026-09-23: high/low are silently discarded unless status=True is
    # sent in the same write.
    coordinator.custom_settings[APPLIANCE_ID] = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": False, "high": 0, "low": 0}, "peak_cut": 100}
    )
    coordinator.api.async_set_custom_settings.return_value = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": True, "high": 28, "low": 23}, "peak_cut": 100}
    )

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)

    _, payload = coordinator.api.async_set_custom_settings.call_args.args
    assert payload["double_mode_temp"] == {"status": True, "high": 28, "low": 23}


async def test_turning_double_temp_off_does_not_flag_the_expected_range_reset(coordinator):
    # Live-confirmed 2026-09-23: turning it off returns status False with the range
    # zeroed, which is not a rejection.
    coordinator.custom_settings[APPLIANCE_ID] = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": True, "high": 28, "low": 23}, "peak_cut": 100}
    )
    coordinator.api.async_set_custom_settings.return_value = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": False, "high": 0, "low": 0}, "peak_cut": 100}
    )

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=False)


async def test_supports_is_permissive_until_functions_are_known(coordinator):
    assert coordinator.supports(APPLIANCE_ID, "smell_care_spot") is True
    coordinator.functions[APPLIANCE_ID] = {"smell_care_spot": False, "smell_care": True}
    assert coordinator.supports(APPLIANCE_ID, "smell_care_spot") is False
    assert coordinator.supports(APPLIANCE_ID, "smell_care") is True
    assert coordinator.supports(APPLIANCE_ID, "not_in_the_list") is True


async def test_update_data_fetches_functions_once_and_failure_is_non_fatal(
    coordinator, initial_status, initial_custom_settings
):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_get_custom_settings.return_value = initial_custom_settings
    coordinator.api.async_get_functions.return_value = {"smell_care": False}

    await coordinator._async_update_data()
    await coordinator._async_update_data()

    coordinator.api.async_get_functions.assert_awaited_once_with("CS-712DX2-W")
    assert coordinator.supports(APPLIANCE_ID, "smell_care") is False


async def test_functions_fetch_failure_keeps_everything_allowed(
    coordinator, initial_status, initial_custom_settings
):
    coordinator.api.async_get_status.return_value = initial_status
    coordinator.api.async_get_custom_settings.return_value = initial_custom_settings
    coordinator.api.async_get_functions.side_effect = EoliaApiError(400, "E-X", "boom")

    await coordinator._async_update_data()

    assert coordinator.supports(APPLIANCE_ID, "smell_care") is True


async def test_powering_off_is_not_flagged_as_a_mode_mismatch(coordinator, initial_status):
    # Off legitimately reports Stop even though the request carried another mode.
    stopped = EoliaStatus.from_dict(
        {
            **initial_status.to_control_fields(),
            "appliance_id": APPLIANCE_ID,
            "operation_status": False,
            "operation_mode": "Stop",
        }
    )
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = stopped

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=False, operation_mode="Auto"
    )

    assert coordinator.data[APPLIANCE_ID] is stopped


@pytest.mark.parametrize(
    ("mode", "running", "expected"),
    [
        ("Cooling", True, "Cooling"),
        ("Nanoe", True, "Blast"),
        ("KeepMode", True, "KeepMode"),
        ("Heating", False, "Auto"),  # not running -> not remembered
        ("Stop", True, "Auto"),
        ("SmellCare", True, "Auto"),  # the clean family is never a "last mode"
    ],
)
async def test_last_mode_is_remembered_for_bare_power_on(
    coordinator, initial_status, mode, running, expected
):
    status = EoliaStatus.from_dict(
        {
            **initial_status.to_control_fields(),
            "appliance_id": APPLIANCE_ID,
            "operation_status": running,
            "operation_mode": mode,
        }
    )
    coordinator._remember_mode(APPLIANCE_ID, status)
    assert coordinator.get_last_mode(APPLIANCE_ID) == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        # Live 2026-09-23: low 23->24 with high 28 was rejected (gap 4).
        ({"double_mode_temp_low": 24}, {"high": 29, "low": 24}),
        ({"double_mode_temp_high": 24}, {"high": 24, "low": 19}),
        # Nudging would leave the valid range, so the request goes out as asked.
        ({"double_mode_temp_low": 27}, {"high": 28, "low": 27}),
        ({"double_mode_temp_high": 21}, {"high": 21, "low": 16}),
        ({"double_mode_temp_high": 19}, {"high": 19, "low": 23}),
    ],
)
async def test_moving_one_bound_within_five_degrees_nudges_the_other(
    coordinator, changes, expected
):
    coordinator.custom_settings[APPLIANCE_ID] = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": True, "high": 28, "low": 23}, "peak_cut": 100}
    )
    coordinator.api.async_set_custom_settings.return_value = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": True, **expected}, "peak_cut": 100}
    )

    await coordinator.async_set_custom_settings(APPLIANCE_ID, **changes)

    _, payload = coordinator.api.async_set_custom_settings.call_args.args
    assert payload["double_mode_temp"] == {"status": True, **expected}


async def test_narrow_range_rejection_gets_an_english_message(coordinator):
    coordinator.custom_settings[APPLIANCE_ID] = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": True, "high": 28, "low": 23}, "peak_cut": 100}
    )
    coordinator.api.async_set_custom_settings.side_effect = EoliaApiError(
        400, "E-21291-02009", "温度設定は5℃以上開くように設定してください。"
    )

    with pytest.raises(HomeAssistantError, match="at least 5 degrees apart"):
        await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_low=27)


async def test_concurrent_writes_are_serialised_so_each_echoes_the_fresh_token(
    coordinator, initial_custom_settings
):
    # Live 2026-09-23: a slider drag fired two writes 0.6s apart carrying the same token;
    # the second got E-21291-01718.
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    sent_tokens: list[str | None] = []

    async def fake_put(appliance_id, payload):
        sent_tokens.append(payload.get("operation_token"))
        await asyncio.sleep(0.01)  # the first response is still in flight
        return EoliaCustomSettings.from_dict(
            {
                "double_mode_temp": payload["double_mode_temp"],
                "peak_cut": 100,
                "operation_token": f"T{len(sent_tokens)}",
            }
        )

    coordinator.api.async_set_custom_settings.side_effect = fake_put

    await asyncio.gather(
        coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_high=28),
        coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_high=27),
    )

    assert sent_tokens == [None, "T1"]


async def test_lockout_error_explains_the_two_minute_rule_on_both_write_paths(
    coordinator, initial_status, initial_custom_settings
):
    locked = EoliaDeviceLockedError(409, "E-21291-01718", "他の機器でエアコンが制御されました。")
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    coordinator.api.async_set_status.side_effect = locked
    coordinator.api.async_set_custom_settings.side_effect = locked

    with pytest.raises(HomeAssistantError, match="about 2 minutes"):
        await coordinator.async_set_status(APPLIANCE_ID, wind_volume=3)
    with pytest.raises(HomeAssistantError, match="about 2 minutes"):
        await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_low=22)


async def test_writes_while_in_nanoe_send_blast_instead(coordinator, initial_status):
    # Live 2026-09-23: turning nanoeX off while the unit read Nanoe sent operation_mode=Nanoe
    # and was rejected (E-21291-01711). Nanoe is a readback only.
    nanoe = _status_with_mode(initial_status, "Nanoe")
    coordinator.async_set_updated_data({APPLIANCE_ID: nanoe})
    coordinator.api.async_set_status.return_value = _status_with_mode(nanoe, "Blast")

    await coordinator.async_set_status(APPLIANCE_ID, nanoex=False)

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["operation_mode"] == "Blast"
    assert payload["nanoex"] is False


async def test_ai_control_silently_reverted_by_the_unit_raises_a_clear_error(
    coordinator, initial_status
):
    # Live 2026-09-23: in Blast/ClothesDryer, ai_control=comfortable gets 200 but is stored
    # as off.
    blast = _status_with_mode(initial_status, "Blast")
    coordinator.async_set_updated_data({APPLIANCE_ID: blast})
    coordinator.api.async_set_status.return_value = blast  # still ai_control off

    with pytest.raises(HomeAssistantError, match="AI isn't available in Blast"):
        await coordinator.async_set_status(APPLIANCE_ID, ai_control="comfortable")

    assert coordinator.data[APPLIANCE_ID] is blast


async def test_dry_humidity_is_restored_from_the_server_after_a_restart(
    coordinator, initial_status, initial_custom_settings
):
    # The Dry target is reported in GET responses while in Dry, so a fresh coordinator
    # (empty cache, e.g. after an HA restart) learns it instead of defaulting to 50.
    dry = EoliaStatus.from_dict({**_dry_body(initial_status), "humidity": 55})
    coordinator.api.async_get_status.return_value = dry
    coordinator.api.async_get_custom_settings.return_value = initial_custom_settings

    await coordinator._async_update_data()

    assert coordinator.get_humidity(APPLIANCE_ID) == 55


def _dry_body(status: EoliaStatus) -> dict:
    return {
        **status.to_control_fields(),
        "appliance_id": status.appliance_id,
        "operation_mode": "ComfortableDehumidification",
    }


async def test_get_humidity_defaults_to_lowest_confirmed_value(coordinator):
    assert coordinator.get_humidity(APPLIANCE_ID) == 50


# --- ClothesDryer also has no user-settable temperature (but, unlike Dry, no humidity
# target either) -- see const.py's ERROR_CODE_TEMPERATURE_OUT_OF_RANGE comment. ---------


async def test_switching_to_clothes_dryer_forces_temp_zero_and_excludes_humidity(
    coordinator, new_status
):
    # new_status (control_response fixture) is Cooling @ 20.0 -- a real, nonzero temp.
    coordinator.async_set_updated_data({APPLIANCE_ID: new_status})
    coordinator.api.async_set_status.return_value = _status_with_mode(
        new_status, "ClothesDryer"
    )

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="ClothesDryer")

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["operation_mode"] == "ClothesDryer"
    assert payload["temperature"] == 0.0
    assert "humidity" not in payload


# --- Switching AWAY from Dry/ClothesDryer must not carry their forced 0.0 into a mode --
# that requires a real temperature. Live-confirmed 2026-09-23: climate.set_preset_mode
# Dry->Cooling with no explicit temperature failed with E-21291-01712 for exactly this
# reason. See const.py's FALLBACK_TEMPERATURE.


async def test_switching_off_dry_mode_substitutes_fallback_temperature_when_uncached(
    coordinator, initial_status
):
    # initial_status (status_response fixture) is ComfortableDehumidification @ 0.0, and
    # nothing else has ever reported a real temperature this session.
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = _status_with_mode(
        initial_status, "Cooling"
    )

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["operation_mode"] == "Cooling"
    assert payload["temperature"] == 24.0  # FALLBACK_TEMPERATURE


async def test_switching_off_dry_mode_uses_last_observed_real_temperature(
    coordinator, initial_status, new_status
):
    # A prior poll (or write) had already seen a real temperature (new_status is
    # Cooling @ 20.0) before the device moved into Dry mode.
    coordinator._remember_temperature(APPLIANCE_ID, new_status)
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = _status_with_mode(
        initial_status, "Cooling"
    )

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["temperature"] == 20.0


async def test_explicit_temperature_is_never_overridden_by_the_fallback(
    coordinator, initial_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = _status_with_mode(
        initial_status, "Cooling"
    )

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_mode="Cooling", temperature=27.0
    )

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["temperature"] == 27.0


# --- operation_token caching (avoids the E-21291-01718 ~2-minute lockout) --------------
# Live-confirmed 2026-09-23 via a controlled CLI A/B test: echoing back the previous
# response's operation_token on the next write avoids the lockout entirely, even for
# writes seconds apart. See tests/fixtures/live_captures/37's notes and coordinator.py's
# _operation_token_cache docstring.

async def test_first_write_has_no_operation_token(coordinator, initial_status, new_status):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")

    _, payload = coordinator.api.async_set_status.call_args.args
    assert "operation_token" not in payload


async def test_second_write_echoes_back_the_first_responses_token(
    coordinator, initial_status, new_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status
    assert new_status.operation_token  # sanity: the fixture really has one

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")
    await coordinator.async_set_status(APPLIANCE_ID, wind_volume=3)

    _, payload = coordinator.api.async_set_status.call_args.args
    assert payload["operation_token"] == new_status.operation_token


async def test_custom_settings_write_also_caches_and_echoes_the_token(
    coordinator, initial_custom_settings
):
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    tokened_settings = EoliaCustomSettings.from_dict(
        {
            "double_mode_temp": {"status": True, "high": 28, "low": 23},
            "peak_cut": 100,
            "operation_token": "TESTTOKEN123",
        }
    )
    coordinator.api.async_set_custom_settings.return_value = tokened_settings

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)
    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_high=28)

    _, payload = coordinator.api.async_set_custom_settings.call_args.args
    assert payload["operation_token"] == "TESTTOKEN123"


async def test_status_and_custom_settings_share_one_token_cache(
    coordinator, initial_status, new_status, initial_custom_settings
):
    # Not confirmed whether the two resources' tokens are actually interchangeable, but
    # the coordinator currently shares one cache between them -- this test just locks in
    # that documented (if unconfirmed) behavior.
    coordinator.async_set_updated_data({APPLIANCE_ID: initial_status})
    coordinator.api.async_set_status.return_value = new_status
    coordinator.custom_settings[APPLIANCE_ID] = initial_custom_settings
    coordinator.api.async_set_custom_settings.return_value = initial_custom_settings

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode="Cooling")
    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)

    _, custom_payload = coordinator.api.async_set_custom_settings.call_args.args
    assert custom_payload["operation_token"] == new_status.operation_token


# --- KeepMode is a dead end for /status (live 2026-09-24, fuzz + direct probe) ---------------


def _with(base: EoliaStatus, **fields) -> EoliaStatus:
    return EoliaStatus.from_dict(
        {**base.to_control_fields(), "appliance_id": APPLIANCE_ID, **fields}
    )


async def test_keep_mode_rejects_status_changes_without_calling_the_api(
    coordinator, initial_status
):
    keep = _with(initial_status, operation_mode="KeepMode", temperature=0.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: keep})

    with pytest.raises(HomeAssistantError, match="KeepMode"):
        await coordinator.async_set_status(APPLIANCE_ID, wind_volume=3)

    coordinator.api.async_set_status.assert_not_awaited()


async def test_keep_mode_power_off_goes_through_customsettings(
    coordinator, initial_status, initial_custom_settings
):
    keep = _with(initial_status, operation_mode="KeepMode", temperature=0.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: keep})
    coordinator.custom_settings[APPLIANCE_ID] = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": True, "high": 28, "low": 23}, "peak_cut": 100}
    )
    coordinator.api.async_set_custom_settings.return_value = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": False, "high": 0, "low": 0}, "peak_cut": 100}
    )

    await coordinator.async_set_status(APPLIANCE_ID, operation_status=False)

    coordinator.api.async_set_status.assert_not_awaited()
    _, payload = coordinator.api.async_set_custom_settings.call_args.args
    assert payload["double_mode_temp"]["status"] is False


async def test_keep_mode_can_be_left_by_writing_a_real_mode(coordinator, initial_status):
    keep = _with(initial_status, operation_mode="KeepMode", temperature=0.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: keep})
    coordinator._temperature_cache[APPLIANCE_ID] = 22.0
    coordinator.api.async_set_status.return_value = _with(
        initial_status, operation_mode="Cooling", temperature=22.0
    )

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Cooling"
    )

    _, payload = coordinator.api.async_set_status.call_args.args
    # KeepMode's own 0.0 temperature must not be carried into a real-temperature mode.
    assert payload["operation_mode"] == "Cooling"
    assert payload["temperature"] == 22.0


# --- Fields the server silently overrides (live 2026-09-24, A/B, live_captures/39) -----------


@pytest.mark.parametrize(
    ("changes", "response", "message"),
    [
        (  # fan is forced to auto while shield/hit is on
            {"wind_volume": 3},
            {"wind_shield_hit": "hit", "wind_volume": 0},
            "keeps the fan and both louvers on auto",
        ),
        (  # ... and while any air_flow value is set
            {"wind_volume": 3},
            {"air_flow": "quiet", "wind_volume": 0},
            "keeps the fan on auto",
        ),
        (
            {"wind_direction": 3},
            {"wind_shield_hit": "shield", "wind_direction": 0},
            "vertical louver change was ignored: wind shield/hit is on",
        ),
        (
            {"wind_direction_horizon": "wide"},
            {"wind_shield_hit": "hit", "wind_direction_horizon": "auto"},
            "horizontal louver change was ignored: wind shield/hit is on",
        ),
        (  # shield/hit is dropped in Blast and ClothesDryer
            {"wind_shield_hit": "hit"},
            {"operation_mode": "Blast", "wind_shield_hit": "not_set"},
            "isn't available in Blast mode",
        ),
        (  # ... and loses to air_flow quiet/long
            {"wind_shield_hit": "hit"},
            {"air_flow": "long", "wind_shield_hit": "not_set"},
            "conflicts with it",
        ),
        (  # air_flow is dropped in Dry
            {"air_flow": "quiet"},
            {"operation_mode": "ComfortableDehumidification", "air_flow": "not_set"},
            "isn't available in ComfortableDehumidification mode",
        ),
        (
            {"wind_volume": 3},
            {"operation_status": False, "operation_mode": "Stop", "wind_volume": 0},
            "off or running a cleaning mode",
        ),
    ],
)
async def test_silently_overridden_fields_raise_an_explanation(
    coordinator, initial_status, changes, response, message
):
    cooling = _with(initial_status, operation_mode="Cooling", temperature=24.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: cooling})
    coordinator.api.async_set_status.return_value = _with(cooling, **response)

    with pytest.raises(HomeAssistantError, match=message):
        await coordinator.async_set_status(APPLIANCE_ID, **changes)

    # The real (overridden) state is still cached, as for the other mismatch checks.
    assert coordinator.data[APPLIANCE_ID].operation_mode == response.get(
        "operation_mode", "Cooling"
    )


async def test_honoured_changes_do_not_raise(coordinator, initial_status):
    cooling = _with(initial_status, operation_mode="Cooling", temperature=24.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: cooling})
    coordinator.api.async_set_status.return_value = _with(cooling, wind_volume=3)

    await coordinator.async_set_status(APPLIANCE_ID, wind_volume=3)


async def test_unrequested_server_side_effects_do_not_raise(coordinator, initial_status):
    # Turning shield/hit on legitimately forces fan and both louvers to auto; the caller only
    # asked for shield/hit, so that is expected behaviour, not an ignored change.
    cooling = _with(
        initial_status,
        operation_mode="Cooling",
        temperature=24.0,
        wind_volume=3,
        wind_direction=4,
        wind_direction_horizon="wide",
    )
    coordinator.async_set_updated_data({APPLIANCE_ID: cooling})
    forced = _with(
        cooling,
        wind_shield_hit="hit",
        wind_volume=0,
        wind_direction=0,
        wind_direction_horizon="auto",
    )
    coordinator.api.async_set_status.return_value = forced

    await coordinator.async_set_status(APPLIANCE_ID, wind_shield_hit="hit")

    assert coordinator.data[APPLIANCE_ID] is forced


# --- Re-reading state after a KeepMode toggle (Kevin saw ~10 s lag, 2026-09-24) ---------------
# KeepMode is entered/left through /customsettings, but the mode every entity and the card
# render from is /status, which used to stay stale until the next 60 s poll.


def _keep_settings(status: bool) -> EoliaCustomSettings:
    return EoliaCustomSettings.from_dict(
        {
            "double_mode_temp": {"status": status, "high": 28 if status else 0, "low": 23 if status else 0},
            "peak_cut": 100,
        }
    )


async def test_entering_keep_mode_rereads_status_so_the_mode_updates_at_once(
    coordinator, initial_status
):
    cooling = _with(initial_status, operation_mode="Cooling")
    coordinator.async_set_updated_data({APPLIANCE_ID: cooling})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(False)
    coordinator.api.async_set_custom_settings.return_value = _keep_settings(True)
    coordinator.api.async_get_status.return_value = _with(
        initial_status, operation_mode="KeepMode", temperature=0.0
    )

    listener_calls = []
    remove = coordinator.async_add_listener(lambda: listener_calls.append(1))
    try:
        await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)
        assert coordinator.data[APPLIANCE_ID].operation_mode == "KeepMode"
        assert listener_calls
    finally:
        remove()
    coordinator.api.async_get_status.assert_awaited_once_with(APPLIANCE_ID)


async def test_status_reread_retries_while_the_server_still_reports_the_old_mode(
    coordinator, initial_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: _with(initial_status, operation_mode="Cooling")})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(False)
    coordinator.api.async_set_custom_settings.return_value = _keep_settings(True)
    stale = _with(initial_status, operation_mode="Cooling")
    fresh = _with(initial_status, operation_mode="KeepMode", temperature=0.0)
    coordinator.api.async_get_status.side_effect = [stale, stale, fresh]

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)

    assert coordinator.api.async_get_status.await_count == 3
    assert coordinator.data[APPLIANCE_ID].operation_mode == "KeepMode"


async def test_status_reread_gives_up_after_a_few_tries_but_keeps_the_freshest(
    coordinator, initial_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: _with(initial_status, operation_mode="Cooling")})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(False)
    coordinator.api.async_set_custom_settings.return_value = _keep_settings(True)
    coordinator.api.async_get_status.return_value = _with(initial_status, operation_mode="Cooling")

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)

    assert coordinator.api.async_get_status.await_count == 3  # bounded, not forever


async def test_turning_keep_mode_off_expects_a_non_keep_mode(coordinator, initial_status):
    keep = _with(initial_status, operation_mode="KeepMode", temperature=0.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: keep})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(True)
    coordinator.api.async_set_custom_settings.return_value = _keep_settings(False)
    coordinator.api.async_get_status.return_value = _with(
        initial_status, operation_mode="Stop", operation_status=False
    )

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=False)

    coordinator.api.async_get_status.assert_awaited_once()  # matched on the first read
    assert coordinator.data[APPLIANCE_ID].operation_mode == "Stop"


async def test_moving_the_range_alone_does_not_reread_status(coordinator, initial_status):
    keep = _with(initial_status, operation_mode="KeepMode", temperature=0.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: keep})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(True)
    coordinator.api.async_set_custom_settings.return_value = EoliaCustomSettings.from_dict(
        {"double_mode_temp": {"status": True, "high": 28, "low": 22}, "peak_cut": 100}
    )

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_low=22)

    coordinator.api.async_get_status.assert_not_awaited()


async def test_a_failed_status_reread_does_not_fail_the_write(coordinator, initial_status):
    cooling = _with(initial_status, operation_mode="Cooling")
    coordinator.async_set_updated_data({APPLIANCE_ID: cooling})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(False)
    new_settings = _keep_settings(True)
    coordinator.api.async_set_custom_settings.return_value = new_settings
    coordinator.api.async_get_status.side_effect = EoliaApiError(500, "E-X", "boom")

    await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)  # no raise

    assert coordinator.custom_settings[APPLIANCE_ID] is new_settings
    assert coordinator.data[APPLIANCE_ID] is cooling  # the poll will catch up


async def test_an_ignored_keep_mode_write_still_raises_after_a_single_reread(
    coordinator, initial_status
):
    coordinator.async_set_updated_data({APPLIANCE_ID: _with(initial_status, operation_mode="Cooling")})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(False)
    # the server answers 200 but keeps status False
    coordinator.api.async_set_custom_settings.return_value = _keep_settings(False)
    coordinator.api.async_get_status.return_value = _with(initial_status, operation_mode="Cooling")

    with pytest.raises(HomeAssistantError, match="didn't apply"):
        await coordinator.async_set_custom_settings(APPLIANCE_ID, double_mode_temp_status=True)

    assert coordinator.api.async_get_status.await_count == 1  # no pointless retries


async def test_leaving_keep_mode_rereads_customsettings_so_the_switch_follows(
    coordinator, initial_status
):
    keep = _with(initial_status, operation_mode="KeepMode", temperature=0.0)
    coordinator.async_set_updated_data({APPLIANCE_ID: keep})
    coordinator.custom_settings[APPLIANCE_ID] = _keep_settings(True)
    coordinator._temperature_cache[APPLIANCE_ID] = 22.0
    coordinator.api.async_set_status.return_value = _with(
        initial_status, operation_mode="Cooling", temperature=22.0
    )
    off_again = _keep_settings(False)
    coordinator.api.async_get_custom_settings.return_value = off_again

    await coordinator.async_set_status(APPLIANCE_ID, operation_status=True, operation_mode="Cooling")

    assert coordinator.custom_settings[APPLIANCE_ID] is off_again


async def test_ordinary_status_writes_do_not_refetch_customsettings(coordinator, initial_status):
    coordinator.async_set_updated_data({APPLIANCE_ID: _with(initial_status, operation_mode="Cooling")})
    coordinator.api.async_set_status.return_value = _with(
        initial_status, operation_mode="Cooling", wind_volume=3
    )

    await coordinator.async_set_status(APPLIANCE_ID, wind_volume=3)

    coordinator.api.async_get_custom_settings.assert_not_awaited()
