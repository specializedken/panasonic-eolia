"""Shared entity base for the Eolia integration."""

from __future__ import annotations

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
