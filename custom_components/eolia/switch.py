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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EoliaConfigEntry
from .coordinator import EoliaDataUpdateCoordinator
from .entity import EoliaEntity
from .models import EoliaCustomSettings


@dataclass(frozen=True, kw_only=True)
class EoliaSwitchEntityDescription(SwitchEntityDescription):
    """Describes an Eolia switch entity."""

    is_on_fn: Callable[[EoliaDataUpdateCoordinator, str], bool]
    control_field: str  # kwarg name passed to coordinator.async_set_status


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
    entities: list[SwitchEntity] = [
        EoliaSwitch(coordinator, appliance_id, description)
        for appliance_id in coordinator.devices
        for description in SWITCH_DESCRIPTIONS
    ]
    entities.extend(
        EoliaDoubleTempEnabledSwitch(coordinator, appliance_id)
        for appliance_id in coordinator.devices
    )
    async_add_entities(entities)


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

    def _require_powered_on(self) -> None:
        """Raise a clear error instead of letting the server's opaque one through.

        Live-confirmed 2026-09-23 via the real HA integration: the API rejects any
        /status field change (not just fan/swing/temperature -- nanoex too) while
        operation_status is False (unit off), with a generic, unhelpful E-21291-01711.
        See climate.py's _require_powered_on for the first occurrence of this.
        """
        status = self._status
        if status is not None and not status.operation_status:
            raise HomeAssistantError(
                f"Can't change {self.entity_description.key} while the AC is off -- "
                "turn it on first."
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._require_powered_on()
        await self.coordinator.async_set_status(
            self._appliance_id, **{self.entity_description.control_field: True}
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._require_powered_on()
        await self.coordinator.async_set_status(
            self._appliance_id, **{self.entity_description.control_field: False}
        )


class EoliaDoubleTempEnabledSwitch(EoliaEntity, SwitchEntity):
    """Toggles KeepMode's double_mode_temp.status -- a separate resource from /status.

    Not built on EoliaSwitchEntityDescription's generic pattern above since it's backed by
    EoliaCustomSettings (.../customsettings), not EoliaStatus (/status), and writes through
    coordinator.async_set_custom_settings() instead of async_set_status(). See
    findings.md's "KeepMode / double temperature setting" section.
    """

    _attr_translation_key = "double_temp_enabled"

    def __init__(
        self, coordinator: EoliaDataUpdateCoordinator, appliance_id: str
    ) -> None:
        super().__init__(coordinator, appliance_id)
        self._attr_unique_id = f"{appliance_id}_double_temp_enabled"

    @property
    def _custom_settings(self) -> EoliaCustomSettings | None:
        return self.coordinator.custom_settings.get(self._appliance_id)

    @property
    def available(self) -> bool:
        """Unavailable if the coordinator failed, or customsettings hasn't loaded yet."""
        return self.coordinator.last_update_success and self._custom_settings is not None

    @property
    def is_on(self) -> bool | None:
        settings = self._custom_settings
        return settings.double_mode_temp.status if settings else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_custom_settings(
            self._appliance_id, double_mode_temp_status=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_custom_settings(
            self._appliance_id, double_mode_temp_status=False
        )
