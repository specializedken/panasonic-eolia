"""Number platform for the Eolia integration.

Currently just KeepMode's ("double temperature setting") low/high range -- a separate
resource from /status (GET/PUT .../customsettings), live-confirmed 2026-09-23. See
findings.md's "KeepMode / double temperature setting" section and
tests/fixtures/live_captures/07 + 16 (the set-double-temp capture).

The server enforces high/low must be at least 5 degrees apart (E-21291-02009 otherwise);
not validated client-side here (mirrors how the temperature-out-of-range error for active
operation_modes isn't client-validated either -- surfaced as a HomeAssistantError instead).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EoliaConfigEntry
from .const import DOUBLE_MODE_TEMP_HIGH_RANGE, DOUBLE_MODE_TEMP_LOW_RANGE
from .coordinator import EoliaDataUpdateCoordinator
from .entity import EoliaEntity
from .models import EoliaCustomSettings


@dataclass(frozen=True, kw_only=True)
class EoliaNumberEntityDescription(NumberEntityDescription):
    """Describes an Eolia number entity backed by EoliaCustomSettings.double_mode_temp."""

    value_fn: Callable[[EoliaCustomSettings], float]
    control_field: str  # kwarg name passed to coordinator.async_set_custom_settings


NUMBER_DESCRIPTIONS: tuple[EoliaNumberEntityDescription, ...] = (
    EoliaNumberEntityDescription(
        key="double_temp_low",
        translation_key="double_temp_low",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=DOUBLE_MODE_TEMP_LOW_RANGE[0],
        native_max_value=DOUBLE_MODE_TEMP_LOW_RANGE[1],
        native_step=1,
        mode=NumberMode.BOX,
        control_field="double_mode_temp_low",
        value_fn=lambda settings: settings.double_mode_temp.low,
    ),
    EoliaNumberEntityDescription(
        key="double_temp_high",
        translation_key="double_temp_high",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=DOUBLE_MODE_TEMP_HIGH_RANGE[0],
        native_max_value=DOUBLE_MODE_TEMP_HIGH_RANGE[1],
        native_step=1,
        mode=NumberMode.BOX,
        control_field="double_mode_temp_high",
        value_fn=lambda settings: settings.double_mode_temp.high,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EoliaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities for every device on the account."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EoliaNumber(coordinator, appliance_id, description)
        for appliance_id in coordinator.devices
        for description in NUMBER_DESCRIPTIONS
    )


class EoliaNumber(EoliaEntity, NumberEntity):
    """A single Eolia number control, backed by EoliaCustomSettings.double_mode_temp."""

    entity_description: EoliaNumberEntityDescription

    def __init__(
        self,
        coordinator: EoliaDataUpdateCoordinator,
        appliance_id: str,
        description: EoliaNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self.entity_description = description
        self._attr_unique_id = f"{appliance_id}_{description.key}"

    @property
    def _custom_settings(self) -> EoliaCustomSettings | None:
        return self.coordinator.custom_settings.get(self._appliance_id)

    @property
    def available(self) -> bool:
        """Unavailable if the coordinator failed, or customsettings hasn't loaded yet."""
        return self.coordinator.last_update_success and self._custom_settings is not None

    @property
    def native_value(self) -> float | None:
        settings = self._custom_settings
        return self.entity_description.value_fn(settings) if settings else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_custom_settings(
            self._appliance_id, **{self.entity_description.control_field: int(value)}
        )
