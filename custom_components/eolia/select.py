"""Select platform for the Eolia integration.

AI mode / ECONAVI are not independently toggleable in this API -- ECONAVI is a third state
of the same `ai_control` field alongside AI-comfort-mode (see findings.md), so a 3-option
select fits better than a boolean switch. air_flow and wind_shield_hit are likewise small,
fixed string enums (live-confirmed against the app 2026-09-23) that map naturally onto
selects rather than switches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EoliaConfigEntry
from .const import EoliaAiControl, EoliaAirFlow, EoliaWindShieldHit
from .coordinator import EoliaDataUpdateCoordinator
from .entity import EoliaEntity
from .models import EoliaStatus


@dataclass(frozen=True, kw_only=True)
class EoliaSelectEntityDescription(SelectEntityDescription):
    """Describes an Eolia select entity backed by a single EoliaStatus string field."""

    current_option_fn: Callable[[EoliaStatus], str]
    control_field: str  # kwarg name passed to coordinator.async_set_status


SELECT_DESCRIPTIONS: tuple[EoliaSelectEntityDescription, ...] = (
    EoliaSelectEntityDescription(
        key="ai_mode",
        translation_key="ai_mode",
        options=[mode.value for mode in EoliaAiControl],
        control_field="ai_control",
        current_option_fn=lambda status: status.ai_control,
    ),
    EoliaSelectEntityDescription(
        key="air_flow",
        translation_key="air_flow",
        options=[mode.value for mode in EoliaAirFlow],
        control_field="air_flow",
        current_option_fn=lambda status: status.air_flow,
    ),
    EoliaSelectEntityDescription(
        key="wind_shield_hit",
        translation_key="wind_shield_hit",
        options=[mode.value for mode in EoliaWindShieldHit],
        control_field="wind_shield_hit",
        current_option_fn=lambda status: status.wind_shield_hit,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EoliaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities for every device on the account."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EoliaSelect(coordinator, appliance_id, description)
        for appliance_id in coordinator.devices
        for description in SELECT_DESCRIPTIONS
    )


class EoliaSelect(EoliaEntity, SelectEntity):
    """A single Eolia select control, backed by one EoliaStatus string field."""

    entity_description: EoliaSelectEntityDescription

    def __init__(
        self,
        coordinator: EoliaDataUpdateCoordinator,
        appliance_id: str,
        description: EoliaSelectEntityDescription,
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self.entity_description = description
        self._attr_unique_id = f"{appliance_id}_{description.key}"

    @property
    def current_option(self) -> str | None:
        status = self._status
        return self.entity_description.current_option_fn(status) if status else None

    async def async_select_option(self, option: str) -> None:
        self._require_powered_on(f"change {self.entity_description.key}")
        await self.coordinator.async_set_status(
            self._appliance_id, **{self.entity_description.control_field: option}
        )
