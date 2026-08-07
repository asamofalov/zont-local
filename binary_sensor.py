from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, EVENT_CONNECTION


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ZontConnectedBinarySensor(hass, entry)])


@dataclass
class _HubDeviceInfo:
    entry_id: str

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry_id)},
            "name": "ZONT Controller",
            "manufacturer": "ZONT",
            "model": "WebSocket Controller",
        }


class ZontConnectedBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._hub = _HubDeviceInfo(entry.entry_id)

        self._attr_unique_id = f"{entry.entry_id}_connected"
        self._attr_device_info = self._hub.device_info

        self._unsub = None

    async def async_added_to_hass(self) -> None:
        # initial state
        client = self._hass.data[DOMAIN][self._entry.entry_id]["client"]
        self._attr_is_on = bool(getattr(client, "is_connected", False))

        @callback
        def _on_connection(event) -> None:
            connected = bool(event.data.get("connected"))
            self._attr_is_on = connected
            self.async_write_ha_state()

        self._unsub = self._hass.bus.async_listen(EVENT_CONNECTION, _on_connection)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None