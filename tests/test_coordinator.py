"""Tests for coordinator.py's read-modify-write PUT-body contract.

async_set_status() is the only place a PUT body gets built. These tests regression-lock
the exact finding that blocked live testing for a day (see findings.md's "RESOLVED"
section): the outgoing payload must never contain applianceId/humidity and must always
contain silence_control, even though silence_control has no GET readback at all.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eolia.const import CONTROL_REQUEST_FIELDS, CUSTOM_SETTINGS_REQUEST_FIELDS
from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator
from custom_components.eolia.exceptions import EoliaApiError
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
    coordinator.api.async_set_status.return_value = new_status

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
            "double_mode_temp": {"status": True, "high": 27, "low": 23},
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
    coordinator.api.async_set_status.return_value = initial_status
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
# Live-confirmed 2026-09-23: requesting MoistCooling got silently downgraded to plain
# Cooling -- 200 OK, no error code, but not what was asked. Same "server accepts but
# doesn't apply" class of bug as the double_mode_temp mismatch check above.


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
