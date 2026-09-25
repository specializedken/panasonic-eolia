"""Switch platform for the Eolia integration.

silence_control (quiet mode) is confirmed write-only: present in every real captured PUT
control request but absent from every GET/PUT .../status response, so its on-device state
can't be read back. It's backed by the coordinator's local cache instead of a status
field, and can go stale if changed via the physical remote or the real app -- see
coordinator.py and findings.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EoliaConfigEntry
from .coordinator import EoliaDataUpdateCoordinator
from .entity import EoliaEntity


@dataclass(frozen=True, kw_only=True)
class EoliaSwitchEntityDescription(SwitchEntityDescription):
    """Describes an Eolia switch entity."""

    is_on_fn: Callable[[EoliaDataUpdateCoordinator, str], bool]
    control_field: str  # kwarg name passed to coordinator.async_set_status
    function_id: str = ""  # /products/{code}/functions flag gating this switch, if any


SWITCH_DESCRIPTIONS: tuple[EoliaSwitchEntityDescription, ...] = (
    EoliaSwitchEntityDescription(
        key="nanoex",
        translation_key="nanoex",
        control_field="nanoex",
        is_on_fn=lambda coordinator, appliance_id: bool(
            coordinator.data[appliance_id].nanoex
        ),
    ),
    EoliaSwitchEntityDescription(
        key="airquality",
        translation_key="air_quality_monitor",
        control_field="airquality",
        function_id="airquality",
        is_on_fn=lambda coordinator, appliance_id: bool(
            coordinator.data[appliance_id].airquality
        ),
    ),
    EoliaSwitchEntityDescription(
        key="silence_control",
        translation_key="silence_control",
        control_field="silence_control",
        is_on_fn=lambda coordinator, appliance_id: coordinator.get_silence_control(
            appliance_id
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EoliaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities for every device on the account."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EoliaSwitch(coordinator, appliance_id, description)
        for appliance_id in coordinator.devices
        for description in SWITCH_DESCRIPTIONS
        # The model's own capability flag (e.g. airquality is false for CS-712DX2-W).
        if coordinator.supports(appliance_id, description.function_id)
    )


class EoliaSwitch(EoliaEntity, SwitchEntity):
    """A single Eolia control toggle."""

    entity_description: EoliaSwitchEntityDescription

    def __init__(
        self,
        coordinator: EoliaDataUpdateCoordinator,
        appliance_id: str,
        description: EoliaSwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self.entity_description = description
        self._attr_unique_id = f"{appliance_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        if self._status is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator, self._appliance_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._require_powered_on(f"turn on {self.entity_description.key}")
        await self.coordinator.async_set_status(
            self._appliance_id, **{self.entity_description.control_field: True}
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._require_powered_on(f"turn off {self.entity_description.key}")
        await self.coordinator.async_set_status(
            self._appliance_id, **{self.entity_description.control_field: False}
        )
