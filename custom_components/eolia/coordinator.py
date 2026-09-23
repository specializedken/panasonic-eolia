"""DataUpdateCoordinator for the Eolia integration.

Owns the read-modify-write contract for control writes: async_set_status() is the ONLY
place a PUT body gets built, starting from the last-known status and applying only the
requested changes. This is deliberate -- see findings.md's "RESOLVED" section for the
hard-won finding that a hand-built PUT body missing the exact right field set (or
including fields the real app never sends) gets rejected with an undiagnosable generic
error. Every entity action funnels through this one method rather than building its own
payload.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EoliaApiClient
from .const import (
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    DOUBLE_MODE_TEMP_HIGH_RANGE,
    DOUBLE_MODE_TEMP_LOW_RANGE,
    DRY_MODE_HUMIDITY_RANGE,
    ERROR_CODE_DOUBLE_TEMP_RANGE_TOO_NARROW,
    FALLBACK_TEMPERATURE,
    CLEAN_FAMILY_MODES,
    NO_TARGET_TEMPERATURE_MODES,
    EoliaOperationMode,
)
from .exceptions import (
    EoliaApiError,
    EoliaAuthError,
    EoliaClockSkewError,
    EoliaDeviceLockedError,
    EoliaNetworkError,
)
from .models import EoliaCustomSettings, EoliaDevice, EoliaStatus

_LOGGER = logging.getLogger(__name__)

# One retry for a transient network failure only -- an application-level E-21291-*
# error never gets auto-retried (resending an identical bad request won't help, and
# repeated failed writes against production infra should stay a conscious action).
_NETWORK_RETRY_DELAY_SECONDS = 2


class EoliaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, EoliaStatus]]):
    """Polls status for every device on the account and serializes control writes."""

    def __init__(
        self, hass: HomeAssistant, api: EoliaApiClient, devices: list[EoliaDevice]
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.api = api
        self.devices: dict[str, EoliaDevice] = {d.appliance_id: d for d in devices}
        # silence_control has no readback in any GET/PUT response (confirmed in
        # findings.md) -- this is the only source of truth for its "current" value, and
        # it can go stale if changed via the physical remote or the real app.
        self._silence_control_cache: dict[str, bool] = {}
        # Dry mode's (ComfortableDehumidification) humidity target: same write-only,
        # no-GET-readback situation as silence_control -- see findings.md and
        # tests/fixtures/live_captures/19. Only ever sent to the server while
        # operation_mode is actually ComfortableDehumidification (see async_set_status);
        # every other mode rejects the `humidity` field entirely.
        self._humidity_cache: dict[str, int] = {}
        # KeepMode's double-temperature range lives on a separate resource
        # (.../customsettings, not /status -- see findings.md and
        # tests/fixtures/live_captures/07). Not part of `self.data` (which
        # DataUpdateCoordinator's own change-notification machinery is keyed on) -- kept
        # as a side-channel dict instead, same pattern as _silence_control_cache. A fetch
        # failure here is non-fatal (logged, cached value kept) since capability gating
        # for this resource isn't confirmed across devices; the main status poll already
        # covers auth/clock-skew failures.
        self.custom_settings: dict[str, EoliaCustomSettings] = {}
        # Live-confirmed 2026-09-23: echoing back the operation_token from the previous
        # PUT response as the next write's `operation_token` avoids the ~2-minute
        # E-21291-01718 "controlled by another device" lockout, even for writes seconds
        # apart -- confirmed with a controlled A/B test via the CLI (2 consecutive
        # writes ~15-20s apart succeeded with the token included; an otherwise-identical
        # write without it failed the same way every previous occurrence did). No
        # readback exists on GET (only PUT responses include it), so this needs its own
        # cache, same pattern as silence_control. Shared across /status and
        # /customsettings since both are scoped to the same physical appliance and both
        # return a token on PUT -- not confirmed whether the two resources' tokens are
        # actually interchangeable, but no evidence yet that they aren't either.
        self._operation_token_cache: dict[str, str] = {}
        # Last real (nonzero) temperature seen for each device, from either a poll or a
        # write response. ComfortableDehumidification/ClothesDryer always report/force
        # temperature=0.0 (see async_set_status), so switching away from either mode into
        # a real-temperature one (e.g. Dry -> Cooling) without this would carry that 0.0
        # straight into an invalid payload -- live-confirmed 2026-09-23, see const.py's
        # FALLBACK_TEMPERATURE.
        self._temperature_cache: dict[str, float] = {}
        # Per-model capability flags from /products/{code}/functions, keyed by
        # appliance_id. Absent = not fetched (yet, or the fetch failed): treated as
        # "unknown, allow everything" so a failure here never removes working features.
        self.functions: dict[str, dict[str, bool]] = {}
        # Last real running mode, so a bare power-on (climate.turn_on) has a mode to use:
        # sending the carried-over "Stop" is rejected with E-21291-01711 (live 2026-09-23).
        # Mirrors the app's own "last drive mode", which also never saves the clean family.
        self._last_mode_cache: dict[str, str] = {}
        # Every write echoes the previous response's operation_token, so two writes in
        # flight at once both carry the same token and the second is rejected as "another
        # device" (E-21291-01718). Live 2026-09-23: dragging a number slider fired two
        # writes 0.6s apart. Serialising them lets each one pick up the fresh token.
        self._write_lock = asyncio.Lock()

    def _remember_mode(self, appliance_id: str, status: EoliaStatus) -> None:
        mode = status.operation_mode
        if not status.operation_status or mode in (
            EoliaOperationMode.STOP,
            EoliaOperationMode.OTHER,
            *CLEAN_FAMILY_MODES,
        ):
            return
        # Nanoe is not requestable; it is what Blast + nanoeX reads back as.
        if mode == EoliaOperationMode.NANOE:
            mode = EoliaOperationMode.BLAST
        self._last_mode_cache[appliance_id] = str(mode)

    def get_last_mode(self, appliance_id: str) -> str:
        return self._last_mode_cache.get(appliance_id, EoliaOperationMode.AUTO.value)

    def supports(self, appliance_id: str, function_id: str) -> bool:
        flags = self.functions.get(appliance_id)
        return True if flags is None else flags.get(function_id, True)

    async def _async_fetch_functions(self, appliance_id: str) -> None:
        if appliance_id in self.functions:
            return
        try:
            self.functions[appliance_id] = await self.api.async_get_functions(
                self.devices[appliance_id].product_code
            )
        except (EoliaApiError, EoliaAuthError) as err:
            _LOGGER.debug("Failed to fetch functions for %s: %s", appliance_id, err)

    @staticmethod
    def _write_error(err: Exception, what: str) -> HomeAssistantError:
        """Build the user-facing error for a failed write."""
        if isinstance(err, EoliaDeviceLockedError):
            # The server's own message is Japanese-only ("controlled by another device").
            return HomeAssistantError(
                "The AC is temporarily locked and won't accept changes for about 2 minutes. "
                "This happens right after another change it can't tell apart from a second "
                "remote: the Eolia app, the physical remote, or the first change after Home "
                "Assistant restarts. Wait 2 minutes, then try again."
            )
        return HomeAssistantError(f"Failed to update {what}: {err}")

    def _remember_humidity(self, appliance_id: str, status: EoliaStatus) -> None:
        # The Dry target IS reported by the server (GET and PUT responses) while in Dry, so
        # an HA restart doesn't have to reset it to the default (live 2026-09-23).
        if (
            status.operation_mode == EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION
            and status.humidity is not None
        ):
            self._humidity_cache[appliance_id] = status.humidity

    def _remember_temperature(self, appliance_id: str, status: EoliaStatus) -> None:
        if status.temperature:
            self._temperature_cache[appliance_id] = status.temperature

    async def _async_update_data(self) -> dict[str, EoliaStatus]:
        statuses: dict[str, EoliaStatus] = {}
        for appliance_id in self.devices:
            statuses[appliance_id] = await self._async_get_status(appliance_id)
            self._remember_temperature(appliance_id, statuses[appliance_id])
            self._remember_mode(appliance_id, statuses[appliance_id])
            self._remember_humidity(appliance_id, statuses[appliance_id])
            await self._async_fetch_functions(appliance_id)
            await self._async_refresh_custom_settings(appliance_id)
        return statuses

    async def _async_refresh_custom_settings(self, appliance_id: str) -> None:
        try:
            self.custom_settings[appliance_id] = await self.api.async_get_custom_settings(
                appliance_id
            )
        except (EoliaApiError, EoliaAuthError) as err:
            _LOGGER.debug(
                "Failed to fetch customsettings for %s, keeping last-known value: %s",
                appliance_id,
                err,
            )

    async def _async_get_status(self, appliance_id: str) -> EoliaStatus:
        try:
            return await self.api.async_get_status(appliance_id)
        except EoliaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EoliaClockSkewError as err:
            raise UpdateFailed(
                f"Eolia server rejected our request timestamp -- check this Home "
                f"Assistant host's system clock: {err}"
            ) from err
        except EoliaNetworkError as err:
            _LOGGER.debug(
                "Transient network error fetching status for %s, retrying once: %s",
                appliance_id,
                err,
            )
            await asyncio.sleep(_NETWORK_RETRY_DELAY_SECONDS)
            try:
                return await self.api.async_get_status(appliance_id)
            except (EoliaApiError, EoliaAuthError) as retry_err:
                raise UpdateFailed(str(retry_err)) from retry_err
        except EoliaApiError as err:
            # Application-level E-21291-* error -- surface immediately, no retry.
            raise UpdateFailed(str(err)) from err

    def get_silence_control(self, appliance_id: str) -> bool:
        """Return the locally-cached silence_control value (no server-side readback exists)."""
        return self._silence_control_cache.get(appliance_id, False)

    def get_humidity(self, appliance_id: str) -> int:
        """Return the locally-cached Dry-mode humidity target (no server-side readback exists)."""
        return self._humidity_cache.get(appliance_id, DRY_MODE_HUMIDITY_RANGE[0])

    async def async_set_status(self, appliance_id: str, **changes: Any) -> None:
        async with self._write_lock:
            await self._async_set_status(appliance_id, **changes)

    async def async_set_custom_settings(self, appliance_id: str, **changes: Any) -> None:
        async with self._write_lock:
            await self._async_set_custom_settings(appliance_id, **changes)

    async def _async_set_status(self, appliance_id: str, **changes: Any) -> None:
        """Apply `changes` on top of the last-known status and PUT the result.

        `changes` keys must match EoliaStatus field names (operation_mode, temperature,
        wind_volume, ...) or "silence_control"/"humidity" (both handled specially, see
        class docstring). Single attempt, no auto-retry -- a failed control write should
        be a deliberate, user-initiated retry, not something that silently fires twice
        against real hardware.
        """
        current = (self.data or {}).get(appliance_id)
        if current is None:
            current = await self.api.async_get_status(appliance_id)

        payload = current.to_control_fields()
        payload["silence_control"] = self.get_silence_control(appliance_id)
        token = self._operation_token_cache.get(appliance_id)
        if token is not None:
            payload["operation_token"] = token

        for key, value in changes.items():
            if key == "silence_control":
                self._silence_control_cache[appliance_id] = value
            elif key == "humidity":
                self._humidity_cache[appliance_id] = value
            payload[key] = value

        # ComfortableDehumidification ("Dry") and ClothesDryer both have no user-settable
        # temperature at all -- the server rejects any nonzero value with E-21291-01712
        # (see const.py's ERROR_CODE_TEMPERATURE_OUT_OF_RANGE). Forced here (not left to
        # callers) so switching into either mode via ANY entity -- climate's preset_mode,
        # a stale cached temperature from whatever mode was active before -- always
        # produces a valid payload, and so a caller can never silently have their
        # temperature request dropped without knowing why (see climate.py's
        # _NO_TARGET_TEMPERATURE_MODES guard, which raises before this is ever reached
        # for a direct temperature-only write).
        if payload["operation_mode"] in NO_TARGET_TEMPERATURE_MODES:
            payload["temperature"] = 0.0
        elif payload["temperature"] == 0.0 and "temperature" not in changes:
            # The mirror-image bug, live-confirmed 2026-09-23: switching AWAY from
            # Dry/ClothesDryer (e.g. via preset_mode) with no explicit temperature given
            # carries their forced 0.0 straight into a mode that requires a real one,
            # failing the exact same E-21291-01712 the block above exists to prevent.
            # Substitute the last real temperature we've observed (or a reasonable
            # default if we've never seen one) rather than let that happen -- never
            # overrides an explicit caller-supplied temperature, only a stale carry-over.
            payload["temperature"] = self._temperature_cache.get(
                appliance_id, FALLBACK_TEMPERATURE
            )

        # ComfortableDehumidification additionally targets humidity, not temperature: the
        # server also requires `humidity` in the payload (E-21291-00007 otherwise) -- the
        # one exception to the general contract, which deliberately excludes humidity for
        # every other mode (including ClothesDryer). See findings.md / const.py's
        # DRY_MODE_HUMIDITY_RANGE.
        if payload["operation_mode"] == EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION:
            payload["humidity"] = changes.get("humidity", self.get_humidity(appliance_id))
        else:
            payload.pop("humidity", None)

        # Nanoe is only ever a readback (Blast with nanoeX on); sending it back is rejected
        # with E-21291-01711 (live 2026-09-23: turning nanoeX off while in Nanoe, so ANY
        # write made while the unit reads Nanoe failed). The nanoex field decides the rest.
        if payload["operation_mode"] == EoliaOperationMode.NANOE:
            payload["operation_mode"] = EoliaOperationMode.BLAST.value

        try:
            new_status = await self.api.async_set_status(appliance_id, payload)
        except (EoliaApiError, EoliaAuthError) as err:
            raise self._write_error(err, "Eolia device") from err

        if new_status.operation_token:
            self._operation_token_cache[appliance_id] = new_status.operation_token
        self._remember_temperature(appliance_id, new_status)
        self._remember_mode(appliance_id, new_status)
        self._remember_humidity(appliance_id, new_status)

        updated = dict(self.data or {})
        updated[appliance_id] = new_status
        self.async_set_updated_data(updated)

        # Live-confirmed 2026-09-23: MoistCooling silently downgrades to plain Cooling
        # when requested via PUT -- 200 OK, but the returned operation_mode doesn't match
        # what was asked, with no error code at all. Same "server accepts a request but
        # doesn't actually apply it" class of bug already guarded against for
        # double_mode_temp below. The one KNOWN, legitimate exception is Blast with
        # nanoex=True, which the device deliberately combines into the NANOE wire value
        # (see const.py's EoliaOperationMode.NANOE) -- that's not a rejection, so it's not
        # flagged here. Only checked when the caller explicitly asked to change
        # operation_mode this call, not when a substitution happens incidentally as a
        # side effect of some other field (e.g. toggling nanoex while already in Blast).
        requested_mode = payload["operation_mode"]
        if (
            "operation_mode" in changes
            # Powering off legitimately reports Stop whatever mode the request carried.
            and payload["operation_status"]
            and new_status.operation_mode != requested_mode
            and not (
                requested_mode == EoliaOperationMode.BLAST
                and new_status.operation_mode == EoliaOperationMode.NANOE
            )
        ):
            raise HomeAssistantError(
                f"Eolia accepted the request but applied a different mode than asked: "
                f"requested {requested_mode!r}, got back {new_status.operation_mode!r}. "
                "This usually means the requested mode isn't actually selectable on "
                "this device via the API, even though it appears in the picker."
            )

        # Live-confirmed 2026-09-23: Blast and ClothesDryer answer 200 but store
        # ai_control=off when asked for an AI mode (the app hides AI there too).
        if "ai_control" in changes and new_status.ai_control != changes["ai_control"]:
            raise HomeAssistantError(
                "The unit ignored the AI mode change -- AI isn't available in "
                f"{new_status.operation_mode} mode."
            )

    async def _async_set_custom_settings(self, appliance_id: str, **changes: Any) -> None:
        """Apply `changes` on top of the last-known .../customsettings and PUT the result.

        `changes` keys are `double_mode_temp_status`/`double_mode_temp_high`/
        `double_mode_temp_low` (flattened for caller convenience -- the wire format nests
        these three under a `double_mode_temp` object) or `peak_cut`. Same single-attempt,
        no-auto-retry contract as async_set_status().
        """
        current = self.custom_settings.get(appliance_id)
        if current is None:
            current = await self.api.async_get_custom_settings(appliance_id)

        payload = current.to_control_fields()
        token = self._operation_token_cache.get(appliance_id)
        if token is not None:
            payload["operation_token"] = token
        double_mode_temp = dict(payload["double_mode_temp"])
        changed_low = "double_mode_temp_low" in changes
        changed_high = "double_mode_temp_high" in changes
        if "double_mode_temp_status" in changes:
            double_mode_temp["status"] = changes.pop("double_mode_temp_status")
        if "double_mode_temp_high" in changes:
            double_mode_temp["high"] = changes.pop("double_mode_temp_high")
        if "double_mode_temp_low" in changes:
            double_mode_temp["low"] = changes.pop("double_mode_temp_low")
        # Live-confirmed 2026-09-23: the server silently discards high/low (returns 0/0
        # with a 200) unless status=True is sent in the SAME write, so turning the setting
        # on with no range yet must send a valid default range along with it. 23/28 are the
        # values the official app itself had saved (tests/fixtures/live_captures/07).
        if (
            double_mode_temp["status"]
            and double_mode_temp["high"] == 0
            and double_mode_temp["low"] == 0
        ):
            double_mode_temp["low"], double_mode_temp["high"] = 23, 28
        # The range resets to 0/0 whenever the unit leaves KeepMode, and each number entity
        # only changes one bound -- so setting either bound alone always sent an invalid
        # range (e.g. high=0, low=16 -> E-21291-02006), live-confirmed 2026-09-23. Fill the
        # untouched, still-unset bound with the nearest valid value that keeps the >=5 gap.
        if double_mode_temp["high"] == 0 and double_mode_temp["low"] != 0:
            double_mode_temp["high"] = min(
                DOUBLE_MODE_TEMP_HIGH_RANGE[1],
                max(DOUBLE_MODE_TEMP_HIGH_RANGE[0], double_mode_temp["low"] + 5),
            )
        elif double_mode_temp["low"] == 0 and double_mode_temp["high"] != 0:
            double_mode_temp["low"] = max(
                DOUBLE_MODE_TEMP_LOW_RANGE[0],
                min(DOUBLE_MODE_TEMP_LOW_RANGE[1], double_mode_temp["high"] - 5),
            )
        # Two separate sliders can't both move at once, so moving one bound within 5 degrees
        # of the other (E-21291-02009, live 2026-09-23) nudges the other bound instead,
        # when that stays inside its valid range; otherwise the server's rejection stands.
        if double_mode_temp["high"] and double_mode_temp["low"]:
            if double_mode_temp["high"] - double_mode_temp["low"] < 5:
                if changed_low and not changed_high:
                    nudged = double_mode_temp["low"] + 5
                    if nudged <= DOUBLE_MODE_TEMP_HIGH_RANGE[1]:
                        double_mode_temp["high"] = nudged
                elif changed_high and not changed_low:
                    nudged = double_mode_temp["high"] - 5
                    if nudged >= DOUBLE_MODE_TEMP_LOW_RANGE[0]:
                        double_mode_temp["low"] = nudged
        payload["double_mode_temp"] = double_mode_temp
        payload.update(changes)  # e.g. peak_cut, if ever needed

        try:
            new_settings = await self.api.async_set_custom_settings(appliance_id, payload)
        except (EoliaApiError, EoliaAuthError) as err:
            if getattr(err, "code", None) == ERROR_CODE_DOUBLE_TEMP_RANGE_TOO_NARROW:
                # The server's own message is Japanese-only.
                raise HomeAssistantError(
                    "The low and high temperatures must be at least 5 degrees apart "
                    f"(and inside {DOUBLE_MODE_TEMP_LOW_RANGE[0]}-"
                    f"{DOUBLE_MODE_TEMP_HIGH_RANGE[1]}C)."
                ) from err
            raise self._write_error(
                err, "Eolia device's double-temperature settings"
            ) from err

        if new_settings.operation_token:
            self._operation_token_cache[appliance_id] = new_settings.operation_token

        self.custom_settings[appliance_id] = new_settings
        self.async_update_listeners()

        # Live-confirmed 2026-09-23: the server can return 200 while silently NOT
        # applying part of the request (e.g. accepting double_mode_temp.status=True
        # but keeping it False when high/low are still the 0/0 leftover from a
        # previous session) -- no error code, so without this check the entity would
        # just report the new (wrong) value as if the write succeeded. Cache the
        # actual returned truth above regardless (so is_on etc. stay correct even
        # when this fires), then surface the mismatch as a real error.
        # Turning the setting OFF legitimately zeroes the range (live-confirmed
        # 2026-09-23), so only the status is comparable then.
        returned = new_settings.double_mode_temp.to_dict()
        if not double_mode_temp["status"]:
            mismatch = returned["status"] != double_mode_temp["status"]
        else:
            mismatch = returned != double_mode_temp
        if mismatch:
            raise HomeAssistantError(
                "Eolia accepted the request but didn't apply it as asked (got back "
                f"{new_settings.double_mode_temp.to_dict()!r}, requested "
                f"{double_mode_temp!r}). The low/high range is only stored while the "
                "double-temperature setting is on, and must be at least 5 degrees apart."
            )
