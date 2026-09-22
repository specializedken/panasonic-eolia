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
from .const import DEFAULT_SCAN_INTERVAL_SECONDS, DOMAIN
from .exceptions import EoliaApiError, EoliaAuthError, EoliaClockSkewError, EoliaNetworkError
from .models import EoliaDevice, EoliaStatus

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

    async def _async_update_data(self) -> dict[str, EoliaStatus]:
        statuses: dict[str, EoliaStatus] = {}
        for appliance_id in self.devices:
            statuses[appliance_id] = await self._async_get_status(appliance_id)
        return statuses

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

    async def async_set_status(self, appliance_id: str, **changes: Any) -> None:
        """Apply `changes` on top of the last-known status and PUT the result.

        `changes` keys must match EoliaStatus field names (operation_mode, temperature,
        wind_volume, ...) or "silence_control" (handled specially, see class docstring).
        Single attempt, no auto-retry -- a failed control write should be a deliberate,
        user-initiated retry, not something that silently fires twice against real
        hardware.
        """
        current = (self.data or {}).get(appliance_id)
        if current is None:
            current = await self.api.async_get_status(appliance_id)

        payload = current.to_control_fields()
        payload["silence_control"] = self.get_silence_control(appliance_id)

        for key, value in changes.items():
            if key == "silence_control":
                self._silence_control_cache[appliance_id] = value
            payload[key] = value

        try:
            new_status = await self.api.async_set_status(appliance_id, payload)
        except (EoliaApiError, EoliaAuthError) as err:
            raise HomeAssistantError(f"Failed to update Eolia device: {err}") from err

        updated = dict(self.data or {})
        updated[appliance_id] = new_status
        self.async_set_updated_data(updated)
