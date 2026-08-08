"""Binary sensors for the ZONT WebSocket integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import connection_signal
from .coordinator import ZontRuntimeData
from .entity import ZontCoordinatorEntity, ZontEntityMixin


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZONT binary sensors."""
    async_add_entities(
        [
            ZontConnectedBinarySensor(entry),
            ZontCloudConnectedBinarySensor(entry),
        ]
    )


class ZontConnectedBinarySensor(ZontEntityMixin, BinarySensorEntity):
    """Represent the ZONT WebSocket connection state."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the sensor."""
        self._entry = entry
        self._attr_is_on = entry.runtime_data.client.is_connected
        self._set_zont_identity(entry, "connected")

    async def async_added_to_hass(self) -> None:
        """Subscribe to connection changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                connection_signal(self._entry.entry_id),
                self._async_connection_changed,
            )
        )

    @callback
    def _async_connection_changed(self, connected: bool) -> None:
        """Handle a WebSocket connection state change."""
        self._attr_is_on = connected
        self.async_write_ha_state()


class ZontCloudConnectedBinarySensor(ZontCoordinatorEntity, BinarySensorEntity):
    """Represent the controller connection to the ZONT cloud."""

    _attr_translation_key = "cloud_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the cloud connection sensor."""
        super().__init__(entry, "cloud_connected")

    @property
    def available(self) -> bool:
        """Return whether server status has been obtained."""
        return super().available and self.controller_data.server_status is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the controller is connected to the ZONT cloud."""
        status = self.controller_data.server_status
        return status.cloud_connected if status is not None else None
