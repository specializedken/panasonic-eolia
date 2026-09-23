"""Climate platform for the Eolia integration.

hvac_mode carries a coarse bucket for standard thermostat-card/voice-assistant
compatibility; preset_mode carries the exact operation_mode wire value as the real
source of truth. This is a deliberate design choice (see the Phase 1 plan) -- HA's fixed
HVACMode enum can't represent Eolia's ~16 operation_mode values, and the whole point of
this project was to expose distinctions like Dry (ComfortableDehumidification) vs Cool &
Dehumidify (CoolDehumidifying), which a coarse bucket alone would erase.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EoliaConfigEntry
from .const import (
    PROVISIONAL_TEMPERATURE_STEP,
    WIND_DIRECTION_LEVELS,
    WIND_VOLUME_LEVELS,
    EoliaOperationMode,
    EoliaWindDirectionHorizon,
)
from .coordinator import EoliaDataUpdateCoordinator
from .entity import EoliaEntity

_LOGGER = logging.getLogger(__name__)

# See findings.md's operation_mode table. Subjective bucketing, flagged in the plan for
# correction against real device behavior -- especially the utility/maintenance modes
# (SmellCare/NanoexCleaning/Cleaning) bucketed under FAN_ONLY as the closest fit, and
# AutoTempControl/KeepMode under AUTO.
_HVAC_MODE_BUCKETS: dict[EoliaOperationMode, HVACMode] = {
    EoliaOperationMode.STOP: HVACMode.OFF,
    EoliaOperationMode.AUTO: HVACMode.AUTO,
    EoliaOperationMode.AUTO_TEMP_CONTROL: HVACMode.AUTO,
    EoliaOperationMode.KEEP_MODE: HVACMode.AUTO,
    EoliaOperationMode.COOLING: HVACMode.COOL,
    EoliaOperationMode.COOL_DEHUMIDIFYING: HVACMode.COOL,
    EoliaOperationMode.MOIST_COOLING: HVACMode.COOL,
    EoliaOperationMode.HEATING: HVACMode.HEAT,
    EoliaOperationMode.KEEP_HEATING: HVACMode.HEAT,
    EoliaOperationMode.BLAST: HVACMode.FAN_ONLY,
    EoliaOperationMode.SMELL_CARE: HVACMode.FAN_ONLY,
    EoliaOperationMode.SMELL_CARE_SPOT: HVACMode.FAN_ONLY,
    EoliaOperationMode.NANOEX_CLEANING: HVACMode.FAN_ONLY,
    EoliaOperationMode.CLEANING: HVACMode.FAN_ONLY,
    EoliaOperationMode.DEHUMIDIFYING: HVACMode.DRY,
    EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION: HVACMode.DRY,
    EoliaOperationMode.CLOTHES_DRYER: HVACMode.DRY,
    # OTHER is intentionally absent -- falls back to the OFF bucket on read, see hvac_mode().
}

# When the user picks a coarse hvac_mode directly (rather than a specific preset_mode),
# this is the representative fine-grained mode actually sent.
_DEFAULT_MODE_FOR_HVAC_MODE: dict[HVACMode, EoliaOperationMode] = {
    HVACMode.AUTO: EoliaOperationMode.AUTO,
    HVACMode.COOL: EoliaOperationMode.COOLING,
    HVACMode.HEAT: EoliaOperationMode.HEATING,
    HVACMode.DRY: EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION,
    HVACMode.FAN_ONLY: EoliaOperationMode.BLAST,
}

# STOP and OTHER are excluded: STOP is handled via hvac_mode OFF (operation_status),
# OTHER is a fallback/unknown value never offered as a user selection.
_SETTABLE_PRESET_MODES = [
    mode.value
    for mode in EoliaOperationMode
    if mode not in (EoliaOperationMode.STOP, EoliaOperationMode.OTHER)
]

_SWING_HORIZONTAL_MODES = [mode.value for mode in EoliaWindDirectionHorizon]
_FAN_MODES = [str(level) for level in WIND_VOLUME_LEVELS]
_SWING_MODES = [str(level) for level in WIND_DIRECTION_LEVELS]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EoliaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one climate entity per device on the account."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EoliaClimateEntity(coordinator, appliance_id)
        for appliance_id in coordinator.devices
    )


class EoliaClimateEntity(EoliaEntity, ClimateEntity):
    """Climate entity for one Eolia device."""

    _attr_name = None  # use the device name
    # Enables preset_mode's state_attributes translation (strings.json's
    # entity.climate.eolia.state_attributes.preset_mode.state) so the UI shows a real
    # label ("Cool & Dehumidify") instead of the raw wire value
    # ("CoolDehumidifying") -- requested 2026-09-23, the cooling/dehumidify family in
    # particular was confusing without it. Does NOT affect the entity's own name:
    # `_attr_name = None` above always wins over any translation-key-driven naming.
    _attr_translation_key = "eolia"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = PROVISIONAL_TEMPERATURE_STEP
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ]
    _attr_preset_modes = _SETTABLE_PRESET_MODES
    _attr_swing_horizontal_modes = _SWING_HORIZONTAL_MODES
    _attr_swing_modes = _SWING_MODES
    _attr_fan_modes = _FAN_MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.SWING_HORIZONTAL_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self, coordinator: EoliaDataUpdateCoordinator, appliance_id: str
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self._attr_unique_id = f"{appliance_id}_climate"

    @property
    def hvac_mode(self) -> HVACMode | None:
        status = self._status
        if status is None:
            return None
        if not status.operation_status:
            return HVACMode.OFF
        try:
            mode = EoliaOperationMode(status.operation_mode)
        except ValueError:
            _LOGGER.warning(
                "Unknown Eolia operation_mode %r, displaying as OFF bucket",
                status.operation_mode,
            )
            return HVACMode.OFF
        bucket = _HVAC_MODE_BUCKETS.get(mode)
        if bucket is None:
            _LOGGER.debug(
                "operation_mode %s has no HVACMode bucket, displaying as OFF bucket", mode
            )
            return HVACMode.OFF
        return bucket

    @property
    def preset_mode(self) -> str | None:
        status = self._status
        if status is None or status.operation_mode == EoliaOperationMode.OTHER:
            return None
        return status.operation_mode

    @property
    def current_temperature(self) -> float | None:
        status = self._status
        return status.inside_temp if status else None

    @property
    def target_temperature(self) -> float | None:
        status = self._status
        return status.temperature if status else None

    @property
    def fan_mode(self) -> str | None:
        status = self._status
        return str(status.wind_volume) if status else None

    @property
    def swing_mode(self) -> str | None:
        status = self._status
        return str(status.wind_direction) if status else None

    @property
    def swing_horizontal_mode(self) -> str | None:
        status = self._status
        return status.wind_direction_horizon if status else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_status(
                self._appliance_id, operation_status=False
            )
            return
        default_mode = _DEFAULT_MODE_FOR_HVAC_MODE.get(hvac_mode)
        if default_mode is None:
            raise HomeAssistantError(f"Unsupported hvac_mode: {hvac_mode}")
        await self.coordinator.async_set_status(
            self._appliance_id, operation_status=True, operation_mode=default_mode.value
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.coordinator.async_set_status(
            self._appliance_id, operation_status=True, operation_mode=preset_mode
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.coordinator.async_set_status(
            self._appliance_id, temperature=float(temperature)
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_set_status(
            self._appliance_id, wind_volume=int(fan_mode)
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.coordinator.async_set_status(
            self._appliance_id, wind_direction=int(swing_mode)
        )

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        await self.coordinator.async_set_status(
            self._appliance_id, wind_direction_horizon=swing_horizontal_mode
        )

    async def async_turn_on(self) -> None:
        await self.coordinator.async_set_status(self._appliance_id, operation_status=True)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set_status(self._appliance_id, operation_status=False)
