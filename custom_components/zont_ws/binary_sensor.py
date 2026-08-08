"""Binary sensors for the ZONT WebSocket integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import ZontWsClient
from .const import DOMAIN, connection_signal


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontWsClient],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZONT binary sensors."""
    async_add_entities([ZontConnectedBinarySensor(entry)])


class ZontConnectedBinarySensor(BinarySensorEntity):
    """Represent the ZONT WebSocket connection state."""

    _attr_has_entity_name = True
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontWsClient]) -> None:
        """Initialize the sensor."""
        self._entry = entry
        self._attr_is_on = entry.runtime_data.is_connected
        controller_identifier = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{controller_identifier}_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller_identifier)},
        )

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
