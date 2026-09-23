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
    CLEAN_FAMILY_MODES,
    NO_TARGET_TEMPERATURE_MODES,
    OPERATION_MODE_FUNCTION_IDS,
    PROVISIONAL_TEMPERATURE_STEP,
    TARGET_TEMPERATURE_RANGE,
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
    EoliaOperationMode.NANOE: HVACMode.FAN_ONLY,
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
# OTHER is a fallback/unknown value never offered as a user selection. DEHUMIDIFYING
# (plain, non-AI dehumidify) is excluded too -- confirmed rejected twice on this device
# (E-21291-01712/E-21291-01711 depending on payload), both via the CLI and the real HA
# integration; the app's own "dehumidification" menu item maps to
# ComfortableDehumidification instead. See findings.md's "What the cooling/dehumidify
# family actually does" section and tests/fixtures/live_captures/19. KEEP_HEATING is
# excluded for the same reason -- live-confirmed 2026-09-23 rejected with E-21291-01711
# (the same generic-error code DEHUMIDIFYING hit) when deliberately selected from the
# real HA dropdown, with an otherwise-valid payload (real temperature, correct field
# set) -- not a coordinator payload bug, just not a real selectable mode on this device.
# NANOE is excluded for a different reason: it's not a mode the API can be asked for at
# all -- live-confirmed 2026-09-23, the server substitutes it automatically for Blast
# when nanoex is on, so it's only ever reachable indirectly (see const.py).
# AUTO_TEMP_CONTROL is excluded because no payload tried so far is accepted: a real 25.0
# gets E-21291-01712 (temperature out of range) and 0.0 gets E-21291-00007 (malformed),
# both live 2026-09-23 via the HA dropdown -- the real request contract is unknown.
_UNSETTABLE_PRESET_MODES = (
    EoliaOperationMode.STOP,
    EoliaOperationMode.OTHER,
    EoliaOperationMode.DEHUMIDIFYING,
    EoliaOperationMode.KEEP_HEATING,
    EoliaOperationMode.NANOE,
    EoliaOperationMode.AUTO_TEMP_CONTROL,
)
_SETTABLE_PRESET_MODES = [
    mode.value for mode in EoliaOperationMode if mode not in _UNSETTABLE_PRESET_MODES
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
    _attr_min_temp = TARGET_TEMPERATURE_RANGE[0]
    _attr_max_temp = TARGET_TEMPERATURE_RANGE[1]
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ]
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
    def preset_modes(self) -> list[str]:
        """Only modes this model supports (per /products/{code}/functions)."""
        return [
            mode
            for mode in _SETTABLE_PRESET_MODES
            if self.coordinator.supports(
                self._appliance_id,
                OPERATION_MODE_FUNCTION_IDS.get(EoliaOperationMode(mode), ""),
            )
        ]

    @property
    def hvac_mode(self) -> HVACMode | None:
        status = self._status
        if status is None:
            return None
        # The clean family runs the unit while operation_status is False (live-confirmed
        # 2026-09-23), so it must be checked before the power test.
        if status.operation_mode in CLEAN_FAMILY_MODES:
            return HVACMode.FAN_ONLY
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
            changes: dict[str, Any] = {"operation_status": False}
            status = self._status
            if status is not None and status.operation_mode in CLEAN_FAMILY_MODES:
                # Already operation_status=False while running, so that alone is a no-op.
                # operation_mode=Stop is rejected (E-21291-01711, live 2026-09-23); send the
                # exact normalized stop body the official app builds (decompiled
                # ControlFetchCommandRHRequest.setData, status=false branch). UNVERIFIED live.
                changes.update(
                    operation_mode=EoliaOperationMode.AUTO.value,
                    temperature=16.0,
                    wind_volume=0,
                    wind_direction=0,
                    wind_direction_horizon="auto",
                )
            await self.coordinator.async_set_status(self._appliance_id, **changes)
            return
        default_mode = _DEFAULT_MODE_FOR_HVAC_MODE.get(hvac_mode)
        if default_mode is None:
            raise HomeAssistantError(f"Unsupported hvac_mode: {hvac_mode}")
        await self.coordinator.async_set_status(
            self._appliance_id, operation_status=True, operation_mode=default_mode.value
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == EoliaOperationMode.KEEP_MODE:
            # Live-confirmed 2026-09-23: operation_mode=KeepMode via /status is always
            # rejected (E-21291-01711). The mode is entered by enabling
            # double_mode_temp.status on /customsettings, which also powers the unit on.
            await self.coordinator.async_set_custom_settings(
                self._appliance_id, double_mode_temp_status=True
            )
            return
        await self.coordinator.async_set_status(
            self._appliance_id, operation_status=True, operation_mode=preset_mode
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._require_powered_on("change the target temperature")
        status = self._status
        if status is not None and status.operation_mode in NO_TARGET_TEMPERATURE_MODES:
            raise HomeAssistantError(
                f"{status.operation_mode} doesn't support a target temperature -- "
                + (
                    "it targets a humidity level instead (use "
                    "number.eolia_dry_humidity_target)."
                    if status.operation_mode
                    == EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION
                    else "temperature is fixed/automatic in this mode."
                )
            )
        await self.coordinator.async_set_status(
            self._appliance_id, temperature=float(temperature)
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._require_powered_on("change the fan speed")
        await self.coordinator.async_set_status(
            self._appliance_id, wind_volume=int(fan_mode)
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        self._require_powered_on("change the vertical swing/louver position")
        await self.coordinator.async_set_status(
            self._appliance_id, wind_direction=int(swing_mode)
        )

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        self._require_powered_on("change the horizontal swing/louver position")
        await self.coordinator.async_set_status(
            self._appliance_id, wind_direction_horizon=swing_horizontal_mode
        )

    async def async_turn_on(self) -> None:
        status = self._status
        if status is not None and status.operation_mode in (
            EoliaOperationMode.STOP,
            EoliaOperationMode.OTHER,
        ):
            # Powering on with the carried-over "Stop" mode is rejected (E-21291-01711,
            # live 2026-09-23), so go through the preset path with the last running mode.
            await self.async_set_preset_mode(
                self.coordinator.get_last_mode(self._appliance_id)
            )
            return
        await self.coordinator.async_set_status(self._appliance_id, operation_status=True)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set_status(self._appliance_id, operation_status=False)
