"""Sensor platform for the Eolia integration.

All values come from the same GET /status the coordinator already polls -- zero extra API
calls. Field availability is not assumed uniform across devices (docs/findings.md flags
per-device capability gating as not fully understood); a None value from the model
naturally makes the entity report `unavailable` rather than a bogus reading.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import EoliaConfigEntry
from .const import (
    DOMAIN,
    DOUBLE_MODE_TEMP_MIN_GAP,
    OPERATION_MODE_TOOLTIPS,
    EoliaOperationMode,
    operation_mode_key,
)
from .controls import applicable_controls
from .coordinator import EoliaDataUpdateCoordinator, EoliaEcoCoordinator
from .entity import EoliaEntity
from .models import EoliaEco, EoliaStatus


@dataclass(frozen=True, kw_only=True)
class EoliaSensorEntityDescription(SensorEntityDescription):
    """Describes an Eolia sensor entity."""

    value_fn: Callable[[EoliaStatus], StateType]
    attrs_fn: Callable[[EoliaStatus], dict[str, Any]] | None = None
    function_id: str = ""  # /products/{code}/functions flag gating this sensor, if any


def _operation_mode(status: EoliaStatus) -> str:
    """The wire `operation_mode` as its HA key, with unknown values folded into `Other`.

    An ENUM sensor raises if its state isn't one of `options`, and the server can return
    modes this integration has never seen (models.py just passes the string through).
    """
    try:
        return operation_mode_key(EoliaOperationMode(status.operation_mode).value)
    except ValueError:
        return operation_mode_key(EoliaOperationMode.OTHER.value)


SENSOR_DESCRIPTIONS: tuple[EoliaSensorEntityDescription, ...] = (
    # The exact operation_mode, not climate's coarse hvac_mode bucket -- exists so
    # dashboards can show/hide mode-dependent controls with plain `state` conditions
    # (Dry vs Cool & Dehumidify, KeepMode, the clean family, Nanoe... all look the same
    # to hvac_mode). Reports `Stop` while off, and the clean family while they run with
    # operation_status false, exactly as the server does.
    EoliaSensorEntityDescription(
        key="operation_mode",
        translation_key="operation_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[operation_mode_key(mode.value) for mode in EoliaOperationMode],
        value_fn=_operation_mode,
        # Which controls currently do anything (controls.py) plus the picker's tooltip text;
        # the Lovelace card renders from these and holds no rules or copy of its own.
        attrs_fn=lambda status: {
            "controls": list(applicable_controls(status)),
            "mode_descriptions": {
                operation_mode_key(mode): text for mode, text in OPERATION_MODE_TOOLTIPS.items()
            },
            "double_temp_min_gap": DOUBLE_MODE_TEMP_MIN_GAP,
        },
    ),
    EoliaSensorEntityDescription(
        key="inside_temp",
        translation_key="indoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.inside_temp,
    ),
    EoliaSensorEntityDescription(
        key="outside_temp",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.outside_temp,
    ),
    EoliaSensorEntityDescription(
        key="inside_humidity",
        translation_key="indoor_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.inside_humidity,
    ),
    # aq_name is the primary, human-meaningful air-quality state. aq_value's numeric scale
    # isn't documented anywhere, so it's a separate, disabled-by-default diagnostic sensor
    # rather than a guess. Both are only created on models with the `airquality` capability:
    # on one without it (CS-712DX2-W) they could only ever read "off" / -1.
    EoliaSensorEntityDescription(
        key="aq_name",
        translation_key="air_quality",
        function_id="airquality",
        value_fn=lambda status: status.aq_name,
    ),
    EoliaSensorEntityDescription(
        key="aq_value",
        translation_key="air_quality_value",
        function_id="airquality",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.aq_value,
    ),
)


@dataclass(frozen=True, kw_only=True)
class EoliaEcoSensorEntityDescription(SensorEntityDescription):
    """Describes an estimated-cost sensor (values come from the eco coordinator)."""

    value_fn: Callable[[EoliaEco], StateType]
    attrs_fn: Callable[[EoliaEco], dict[str, Any]] | None = None


# Panasonic's own app-side estimate in yen -- there is no watts/kWh anywhere in the cloud API
# (docs/findings.md, "Power/eco history"). Deliberately no state_class: these are Panasonic's
# rounded estimates, not a meter, so they don't belong in the Energy dashboard's statistics.
ECO_SENSOR_DESCRIPTIONS: tuple[EoliaEcoSensorEntityDescription, ...] = (
    # While running: the current run's cost so far. Once stopped: the previous run's cost.
    EoliaEcoSensorEntityDescription(
        key="cost_latest",
        translation_key="cost_latest",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="JPY",
        value_fn=lambda eco: eco.latest_bill,
    ),
    EoliaEcoSensorEntityDescription(
        key="cost_this_month",
        translation_key="cost_this_month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="JPY",
        value_fn=lambda eco: eco.this_month_bill,
        # Twelve monthly totals, January first, for the Lovelace/history use.
        attrs_fn=lambda eco: {
            "this_year_monthly": list(eco.this_year_bills),
            "last_year_monthly": list(eco.last_year_bills),
        },
    ),
    EoliaEcoSensorEntityDescription(
        key="cost_last_month",
        translation_key="cost_last_month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="JPY",
        value_fn=lambda eco: eco.last_month_bill,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EoliaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for every device on the account."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EoliaSensor(coordinator, appliance_id, description)
        for appliance_id in coordinator.devices
        for description in SENSOR_DESCRIPTIONS
        # The model's own capability flag (unknown flags allow, see coordinator.supports()).
        if coordinator.supports(appliance_id, description.function_id)
    )
    eco_coordinator = entry.runtime_data.eco_coordinator
    async_add_entities(
        EoliaEcoSensor(eco_coordinator, coordinator, appliance_id, description)
        for appliance_id in coordinator.devices
        for description in ECO_SENSOR_DESCRIPTIONS
    )


class EoliaSensor(EoliaEntity, SensorEntity):
    """A single read-only Eolia status field."""

    entity_description: EoliaSensorEntityDescription
    # Static text that never changes; no reason to store it with every state change.
    _unrecorded_attributes = frozenset({"mode_descriptions"})

    def __init__(
        self,
        coordinator: EoliaDataUpdateCoordinator,
        appliance_id: str,
        description: EoliaSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self.entity_description = description
        self._attr_unique_id = f"{appliance_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        status = self._status
        if status is None:
            return None
        return self.entity_description.value_fn(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = self._status
        attrs_fn = self.entity_description.attrs_fn
        if status is None or attrs_fn is None:
            return None
        return attrs_fn(status)


class EoliaEcoSensor(CoordinatorEntity[EoliaEcoCoordinator], SensorEntity):
    """One of Panasonic's estimated electricity-cost figures (yen)."""

    _attr_has_entity_name = True
    entity_description: EoliaEcoSensorEntityDescription

    def __init__(
        self,
        eco_coordinator: EoliaEcoCoordinator,
        coordinator: EoliaDataUpdateCoordinator,
        appliance_id: str,
        description: EoliaEcoSensorEntityDescription,
    ) -> None:
        super().__init__(eco_coordinator)
        self._appliance_id = appliance_id
        self.entity_description = description
        self._attr_unique_id = f"{appliance_id}_{description.key}"
        device = coordinator.devices[appliance_id]
        # Same identifiers as every other Eolia entity, so this lands on the AC's device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, appliance_id)},
            name=device.nickname,
            manufacturer="Panasonic",
            model=device.product_code,
        )

    @property
    def _eco(self) -> EoliaEco | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._appliance_id)

    @property
    def available(self) -> bool:
        return super().available and self._eco is not None

    @property
    def native_value(self) -> StateType:
        eco = self._eco
        return None if eco is None else self.entity_description.value_fn(eco)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        eco = self._eco
        attrs_fn = self.entity_description.attrs_fn
        if eco is None or attrs_fn is None:
            return None
        return attrs_fn(eco)
