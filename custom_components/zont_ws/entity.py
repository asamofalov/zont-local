"""Common entities for the ZONT integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    ZontControllerData,
    ZontDataUpdateCoordinator,
    ZontRuntimeData,
)


class ZontEntityMixin:
    """Provide stable ZONT entity and device registry identifiers."""

    _attr_has_entity_name = True

    def _set_zont_identity(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        unique_id_suffix: str,
    ) -> None:
        """Link an entity to its controller and set stable identifier suffixes."""
        controller_identifier = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{controller_identifier}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller_identifier)},
        )
        self._zont_object_id_suffix = unique_id_suffix

    @property
    def suggested_object_id(self) -> str:
        """Return a stable suffix for newly generated entity IDs."""
        return self._zont_object_id_suffix


class ZontCoordinatorEntity(
    ZontEntityMixin, CoordinatorEntity[ZontDataUpdateCoordinator]
):
    """Base for ZONT entities backed by the shared data snapshot."""

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        unique_id_suffix: str,
    ) -> None:
        """Initialize an entity linked to the controller device."""
        super().__init__(entry.runtime_data.coordinator)
        self._set_zont_identity(entry, unique_id_suffix)

    @property
    def controller_data(self) -> ZontControllerData:
        """Return the current controller part of the shared snapshot."""
        return self.coordinator.data.controller
