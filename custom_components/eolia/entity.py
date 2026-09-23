"""Shared entity base for the Eolia integration."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EoliaDataUpdateCoordinator
from .models import EoliaStatus


class EoliaEntity(CoordinatorEntity[EoliaDataUpdateCoordinator]):
    """Base class for all Eolia entities, tied to one appliance_id."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EoliaDataUpdateCoordinator, appliance_id: str) -> None:
        super().__init__(coordinator)
        self._appliance_id = appliance_id
        device = coordinator.devices[appliance_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, appliance_id)},
            name=device.nickname,
            manufacturer="Panasonic",
            model=device.product_code,
        )

    @property
    def _status(self) -> EoliaStatus | None:
        """Return the coordinator's last-known status for this device, if any."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._appliance_id)

    @property
    def available(self) -> bool:
        """Unavailable if the coordinator itself failed, or this device has no status yet."""
        return super().available and self._status is not None

    def _require_powered_on(self, action: str) -> None:
        """Raise a clear error instead of letting the server's opaque one through.

        Live-confirmed 2026-09-23 via the real HA integration: the API rejects ANY
        /status field change (temperature, fan, swing, nanoex, ai_control, ... --
        every one tried) while `operation_status` is False (unit off), with a generic,
        unhelpful E-21291-01711 ("an application error occurred"). `preset_mode`/
        `hvac_mode` on the climate entity sidestep this by forcing
        `operation_status=True` alongside their own change (mirroring how the
        physical remote/app work: picking a mode turns the unit on); every other
        control (temperature, fan/swing on climate; the select entities; the
        EoliaStatus-backed switches) has no mode of its own to piggyback that on, so
        they call this instead of also implicitly powering the unit on as a
        surprising side effect.
        """
        status = self._status
        if status is not None and not status.operation_status:
            raise HomeAssistantError(f"Can't {action} while the AC is off -- turn it on first.")
