"""Number platform for the Eolia integration.

Two independent features live here:
- KeepMode's ("double temperature setting") low/high range -- a separate resource from
  /status (GET/PUT .../customsettings), live-confirmed 2026-09-23. See docs/findings.md's
  "KeepMode / double temperature setting" section and tests/fixtures/live_captures/07 + 16
  (the set-double-temp capture). The server enforces high/low must be at least 5 degrees
  apart (E-21291-02009 otherwise); not validated client-side here (mirrors how the
  temperature-out-of-range error for active operation_modes isn't client-validated either
  -- surfaced as a HomeAssistantError instead).
- Dry mode's (ComfortableDehumidification) humidity target -- a /status field, not a
  separate resource, but with no GET readback at all (write-only, same situation as
  silence_control) and a server-enforced range far narrower than a normal humidity slider:
  exactly 50/55/60 (5% steps), not a continuous 0-100% range. A `number` entity is used
  here instead of ClimateEntity's native target_humidity specifically because
  ClimateEntity has no humidity-step concept -- it'd let a user pick e.g. 52% and get a
  confusing rejection from the server. Only meaningful while operation_mode is actually
  ComfortableDehumidification; see coordinator.py's async_set_status for how writes to it
  get folded into a valid payload regardless of which entity triggered them. See
  tests/fixtures/live_captures/19-24.
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
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EoliaConfigEntry
from .const import (
    DOUBLE_MODE_TEMP_HIGH_RANGE,
    DOUBLE_MODE_TEMP_LOW_RANGE,
    DRY_MODE_HUMIDITY_RANGE,
    DRY_MODE_HUMIDITY_STEP,
    EoliaOperationMode,
)
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
    entities: list[NumberEntity] = [
        EoliaNumber(coordinator, appliance_id, description)
        for appliance_id in coordinator.devices
        for description in NUMBER_DESCRIPTIONS
    ]
    entities.extend(
        EoliaDryHumidityNumber(coordinator, appliance_id)
        for appliance_id in coordinator.devices
    )
    async_add_entities(entities)


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


class EoliaDryHumidityNumber(EoliaEntity, NumberEntity):
    """Dry mode's (ComfortableDehumidification) humidity target.

    Backed by coordinator.get_humidity() (a local cache, no GET readback exists -- see
    module docstring), not EoliaCustomSettings -- doesn't fit EoliaNumberEntityDescription's
    pattern above, so it's a dedicated class instead.
    """

    _attr_translation_key = "dry_humidity_target"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = DRY_MODE_HUMIDITY_RANGE[0]
    _attr_native_max_value = DRY_MODE_HUMIDITY_RANGE[1]
    _attr_native_step = DRY_MODE_HUMIDITY_STEP
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: EoliaDataUpdateCoordinator, appliance_id: str
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self._attr_unique_id = f"{appliance_id}_dry_humidity_target"

    @property
    def available(self) -> bool:
        """Only meaningful while the unit is actually in Dry mode."""
        status = self._status
        return (
            self.coordinator.last_update_success
            and status is not None
            and status.operation_mode == EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.get_humidity(self._appliance_id)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_status(self._appliance_id, humidity=int(value))
