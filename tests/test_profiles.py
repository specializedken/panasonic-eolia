"""Per-mode profiles: switching to a mode (or powering on into it) restores how it was set up.

Uses a fake unit that applies each PUT as sent, the way the real server does when nothing
conflicts, and reports Stop once it is powered off.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.eolia.coordinator import EoliaDataUpdateCoordinator, _profile_key
from custom_components.eolia.models import EoliaDevice, EoliaStatus

APPLIANCE_ID = "EXAMPLEAPPLIANCEID0000000000000000000000000="
DRY = "ComfortableDehumidification"


def _status(**fields) -> EoliaStatus:
    return EoliaStatus.from_dict({"appliance_id": APPLIANCE_ID, **fields})


def _put(appliance_id: str, payload: dict) -> EoliaStatus:
    fields = dict(payload)
    if not fields["operation_status"]:
        fields["operation_mode"] = "Stop"
    return EoliaStatus.from_dict({**fields, "appliance_id": appliance_id})


@pytest.fixture
def coordinator(hass) -> EoliaDataUpdateCoordinator:
    api = AsyncMock()
    api.async_set_status.side_effect = _put
    device = EoliaDevice(
        appliance_id=APPLIANCE_ID, nickname="Yurt", product_code="CS-712DX2-W", product_name="t"
    )
    coord = EoliaDataUpdateCoordinator(hass, api, [device])
    coord.async_set_updated_data(
        {APPLIANCE_ID: _status(operation_status=True, operation_mode="Cooling", temperature=24.0)}
    )
    return coord


def _last_payload(coordinator) -> dict:
    return coordinator.api.async_set_status.call_args.args[1]


async def _dry_setup(coordinator) -> None:
    """The user's example: Dry, 60 %, AI on, nanoeX on."""
    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode=DRY, humidity=60,
        ai_control="comfortable", nanoex=True,
    )  # fmt: skip


async def test_returning_to_a_mode_restores_its_settings(coordinator):
    await _dry_setup(coordinator)
    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Cooling"
    )
    # Cooling has its own settings now; drop the ones Dry set so a leak would show.
    await coordinator.async_set_status(APPLIANCE_ID, ai_control="off", nanoex=False)

    await coordinator.async_set_status(APPLIANCE_ID, operation_status=True, operation_mode=DRY)

    payload = _last_payload(coordinator)
    assert payload["operation_mode"] == DRY
    assert payload["humidity"] == 60
    assert payload["ai_control"] == "comfortable"
    assert payload["nanoex"] is True


async def test_powering_on_from_off_restores_the_mode(coordinator):
    await _dry_setup(coordinator)
    await coordinator.async_set_status(APPLIANCE_ID, operation_status=False)
    # Whatever the unit reports while off must not leak into the restored mode.
    coordinator.async_set_updated_data(
        {APPLIANCE_ID: _status(operation_status=False, operation_mode="Stop", nanoex=False)}
    )

    await coordinator.async_set_status(APPLIANCE_ID, operation_status=True, operation_mode=DRY)

    payload = _last_payload(coordinator)
    assert (payload["humidity"], payload["ai_control"], payload["nanoex"]) == (
        60, "comfortable", True,
    )  # fmt: skip


async def test_each_mode_keeps_its_own_temperature(coordinator):
    await coordinator.async_set_status(APPLIANCE_ID, temperature=22.0)  # Cooling 22
    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Heating", temperature=27.5
    )

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Cooling"
    )
    assert _last_payload(coordinator)["temperature"] == 22.0

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Heating"
    )
    assert _last_payload(coordinator)["temperature"] == 27.5


async def test_an_explicit_value_beats_the_saved_one(coordinator):
    await _dry_setup(coordinator)
    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Cooling"
    )

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode=DRY, humidity=45, nanoex=False
    )

    payload = _last_payload(coordinator)
    assert payload["humidity"] == 45
    assert payload["nanoex"] is False
    assert payload["ai_control"] == "comfortable"  # not asked for, so still restored


async def test_a_write_inside_the_running_mode_does_not_replay_the_profile(coordinator):
    await _dry_setup(coordinator)
    coordinator._profiles[APPLIANCE_ID][DRY]["wind_volume"] = 5  # a stale saved value

    await coordinator.async_set_status(APPLIANCE_ID, ai_control="off")

    assert _last_payload(coordinator)["wind_volume"] == 0


async def test_a_mode_with_no_profile_yet_is_left_as_before(coordinator):
    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Heating"
    )

    payload = _last_payload(coordinator)
    assert payload["operation_mode"] == "Heating"
    assert payload["temperature"] == 24.0  # carried over, as before profiles existed


async def test_polling_records_changes_made_elsewhere(coordinator):
    coordinator.api.async_get_status.return_value = _status(
        operation_status=True, operation_mode="Heating", temperature=26.0, wind_volume=3,
        ai_control="comfortable",
    )  # fmt: skip
    await coordinator._async_update_data()

    assert coordinator._profiles[APPLIANCE_ID]["Heating"]["temperature"] == 26.0
    assert coordinator._profiles[APPLIANCE_ID]["Heating"]["wind_volume"] == 3


async def test_off_and_unrestorable_modes_are_not_recorded(coordinator):
    for fields in (
        {"operation_status": False, "operation_mode": "Stop"},
        {"operation_status": True, "operation_mode": "KeepMode"},
        {"operation_status": False, "operation_mode": "Cleaning"},
    ):
        coordinator.api.async_get_status.return_value = _status(**fields)
        await coordinator._async_update_data()

    assert not coordinator._profiles.get(APPLIANCE_ID)


def test_nanoe_files_under_blast_and_the_rest_are_unfiled():
    assert _profile_key("Nanoe") == "Blast"
    assert _profile_key("Cooling") == "Cooling"
    assert all(_profile_key(m) is None for m in ("Stop", "Other", "KeepMode", "Cleaning"))


async def test_quiet_mode_is_remembered_per_mode(coordinator):
    await coordinator.async_set_status(APPLIANCE_ID, silence_control=True)  # Cooling: quiet
    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Heating", silence_control=False
    )

    await coordinator.async_set_status(
        APPLIANCE_ID, operation_status=True, operation_mode="Cooling"
    )

    assert _last_payload(coordinator)["silence_control"] is True
    assert coordinator.get_silence_control(APPLIANCE_ID) is True


async def test_profiles_survive_a_restart(hass, coordinator, hass_storage):
    await _dry_setup(coordinator)
    await coordinator.async_set_status(APPLIANCE_ID, operation_status=False)

    reborn = EoliaDataUpdateCoordinator(hass, AsyncMock(), list(coordinator.devices.values()))
    await reborn.async_load_profiles()

    assert reborn._profiles[APPLIANCE_ID][DRY]["humidity"] == 60
    assert reborn._profiles[APPLIANCE_ID][DRY]["ai_control"] == "comfortable"
    assert reborn.get_last_mode(APPLIANCE_ID) == DRY


async def test_a_restart_does_not_overwrite_saved_quiet_mode_with_the_default(
    hass, coordinator
):
    await coordinator.async_set_status(APPLIANCE_ID, silence_control=True)  # Cooling: quiet

    reborn = EoliaDataUpdateCoordinator(hass, AsyncMock(), list(coordinator.devices.values()))
    await reborn.async_load_profiles()
    reborn.api.async_get_status.return_value = _status(
        operation_status=True, operation_mode="Cooling", temperature=22.0
    )
    await reborn._async_update_data()

    assert reborn.get_silence_control(APPLIANCE_ID) is True
    assert reborn._profiles[APPLIANCE_ID]["Cooling"]["silence_control"] is True


async def test_unchanged_profiles_are_not_rewritten(coordinator, hass_storage):
    await _dry_setup(coordinator)
    coordinator._store.async_save = AsyncMock(wraps=coordinator._store.async_save)

    await coordinator.async_set_status(APPLIANCE_ID, operation_mode=DRY)  # nothing new
    coordinator._store.async_save.assert_not_awaited()
