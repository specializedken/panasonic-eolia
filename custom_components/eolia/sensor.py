"""Sensor platform for the Eolia integration.

All values come from the same GET /status the coordinator already polls -- zero extra API
calls. Field availability is not assumed uniform across devices (findings.md flags
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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import EoliaConfigEntry
from .const import DOUBLE_MODE_TEMP_MIN_GAP, OPERATION_MODE_TOOLTIPS, EoliaOperationMode
from .controls import applicable_controls
from .coordinator import EoliaDataUpdateCoordinator
from .entity import EoliaEntity
from .models import EoliaStatus


@dataclass(frozen=True, kw_only=True)
class EoliaSensorEntityDescription(SensorEntityDescription):
    """Describes an Eolia sensor entity."""

    value_fn: Callable[[EoliaStatus], StateType]
    attrs_fn: Callable[[EoliaStatus], dict[str, Any]] | None = None


def _operation_mode(status: EoliaStatus) -> str:
    """The raw wire `operation_mode`, with unknown values folded into `Other`.

    An ENUM sensor raises if its state isn't one of `options`, and the server can return
    modes this integration has never seen (models.py just passes the string through).
    """
    try:
        return EoliaOperationMode(status.operation_mode).value
    except ValueError:
        return EoliaOperationMode.OTHER.value


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
        options=[mode.value for mode in EoliaOperationMode],
        value_fn=_operation_mode,
        # Which controls currently do anything (controls.py) plus the picker's tooltip text;
        # the Lovelace card renders from these and holds no rules or copy of its own.
        attrs_fn=lambda status: {
            "controls": list(applicable_controls(status)),
            "mode_descriptions": OPERATION_MODE_TOOLTIPS,
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
    # aq_name is the primary, human-meaningful air-quality state (e.g. "off" when
    # monitoring is disabled -- see findings.md, this is expected on a device with
    # airquality: false, not a bug). aq_value's numeric scale isn't documented anywhere,
    # so it's a separate, disabled-by-default diagnostic sensor rather than a guess.
    EoliaSensorEntityDescription(
        key="aq_name",
        translation_key="air_quality",
        value_fn=lambda status: status.aq_name,
    ),
    EoliaSensorEntityDescription(
        key="aq_value",
        translation_key="air_quality_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.aq_value,
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
