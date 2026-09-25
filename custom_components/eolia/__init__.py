"""The Eolia integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import EoliaApiClient
from .auth import EoliaAuth
from .const import CONF_ACCESS_TOKEN, CONF_EXPIRES_AT, CONF_REFRESH_TOKEN
from .coordinator import EoliaDataUpdateCoordinator
from .exceptions import EoliaApiError, EoliaAuthError
from .frontend import async_register_card

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class EoliaRuntimeData:
    """Data stored on the config entry at runtime."""

    api: EoliaApiClient
    coordinator: EoliaDataUpdateCoordinator


type EoliaConfigEntry = ConfigEntry[EoliaRuntimeData]

# Entities this integration used to create and no longer does, as unique_id suffixes. Their
# registry entries would otherwise stay behind as "no longer provided" ghosts forever.
# - _double_temp_enabled: the Double temperature on/off switch (removed 2026-09-25; picking or
#   leaving the Double temperature mode does the same job).
_RETIRED_UNIQUE_ID_SUFFIXES = ("_double_temp_enabled",)


@callback
def async_remove_retired_entities(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Delete registry entries for entities the integration no longer provides."""
    registry = er.async_get(hass)
    removed = []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.endswith(_RETIRED_UNIQUE_ID_SUFFIXES):
            registry.async_remove(entity.entity_id)
            removed.append(entity.entity_id)
    return removed


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled Lovelace card (once per HA start, not per config entry)."""
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EoliaConfigEntry) -> bool:
    """Set up Eolia from a config entry."""
    session = async_get_clientsession(hass)

    async def _async_on_tokens_updated(new_tokens: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **new_tokens})

    auth = EoliaAuth(
        session,
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        expires_at=entry.data[CONF_EXPIRES_AT],
        on_tokens_updated=_async_on_tokens_updated,
    )
    api = EoliaApiClient(session, auth)

    try:
        devices = await api.async_get_devices()
    except EoliaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except EoliaApiError as err:
        raise ConfigEntryNotReady(f"Could not reach Eolia API: {err}") from err

    if not devices:
        _LOGGER.warning("Eolia account has no registered devices")

    coordinator = EoliaDataUpdateCoordinator(hass, api, devices)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EoliaRuntimeData(api=api, coordinator=coordinator)

    for entity_id in async_remove_retired_entities(hass, entry):
        _LOGGER.info("Removed retired Eolia entity %s", entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EoliaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
