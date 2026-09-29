"""The Eolia integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import EoliaApiClient
from .auth import EoliaAuth
from .const import CONF_ACCESS_TOKEN, CONF_EXPIRES_AT, CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import EoliaDataUpdateCoordinator
from .exceptions import EoliaApiError, EoliaAuthError
from .frontend import async_register_card

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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


# Entities that only exist on models with a given capability flag (GET /products/{code}/functions),
# as unique_id suffix -> flag. The platforms already don't create them for a model without the
# flag, but a registry entry left from before that gating (or from a model swap) would sit on
# the device as a dead "unavailable" entity -- and the Lovelace card would show a row for it.
_MODEL_GATED_UNIQUE_ID_SUFFIXES = {
    "_airquality": "airquality",  # the air-quality monitoring switch
    "_aq_name": "airquality",  # the air-quality sensor
    "_aq_value": "airquality",  # its raw-value diagnostic sensor
}


@callback
def async_remove_unsupported_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: EoliaDataUpdateCoordinator
) -> list[str]:
    """Delete registry entries for features the device's model reports it doesn't have.

    Only acts on a flag the cloud actually reported as false: `supports()` answers True when
    the flags are unknown, so a failed capability fetch never deletes anything.
    """
    registry = er.async_get(hass)
    removed = []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        for suffix, function_id in _MODEL_GATED_UNIQUE_ID_SUFFIXES.items():
            if not entity.unique_id.endswith(suffix):
                continue
            appliance_id = entity.unique_id[: -len(suffix)]
            if not coordinator.supports(appliance_id, function_id):
                registry.async_remove(entity.entity_id)
                removed.append(entity.entity_id)
    return removed


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
    await coordinator.async_load_profiles()
    # Deliberately NOT async_config_entry_first_refresh(): that turns a failed first read into
    # ConfigEntryNotReady, and while the AC is unreachable (E-21291-01602: off at the wall, or
    # off Wi-Fi) the whole entry then sat in HA's setup-retry loop, every entity a dead
    # "unavailable" placeholder, until a retry happened to land. Loading anyway makes the
    # entities unavailable-but-alive: the normal 60 s poll revives them the moment the unit is
    # back. A rejected login still starts reauth (async_refresh handles that itself).
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        _LOGGER.info(
            "Eolia loaded without a first status (%s); entities are unavailable until the "
            "next poll succeeds",
            coordinator.last_exception,
        )

    entry.runtime_data = EoliaRuntimeData(api=api, coordinator=coordinator)

    for entity_id in (
        *async_remove_retired_entities(hass, entry),
        *async_remove_unsupported_entities(hass, entry, coordinator),
    ):
        _LOGGER.info("Removed Eolia entity %s (retired, or not supported by this model)", entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EoliaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
