"""Select platform for the Eolia integration.

AI mode / ECONAVI are not independently toggleable in this API -- ECONAVI is a third state
of the same `ai_control` field alongside AI-comfort-mode (see findings.md), so a 3-option
select fits better than a boolean switch.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EoliaConfigEntry
from .const import EoliaAiControl
from .coordinator import EoliaDataUpdateCoordinator
from .entity import EoliaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EoliaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one AI-mode select entity per device on the account."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EoliaAiModeSelect(coordinator, appliance_id)
        for appliance_id in coordinator.devices
    )


class EoliaAiModeSelect(EoliaEntity, SelectEntity):
    """Selects the ai_control field: off / comfortable / comfortable_econavi."""

    _attr_translation_key = "ai_mode"
    _attr_options = [mode.value for mode in EoliaAiControl]

    def __init__(
        self, coordinator: EoliaDataUpdateCoordinator, appliance_id: str
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self._attr_unique_id = f"{appliance_id}_ai_mode"

    @property
    def current_option(self) -> str | None:
        status = self._status
        return status.ai_control if status else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_status(self._appliance_id, ai_control=option)
