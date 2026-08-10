"""Buttons for the ZONT integration."""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import ZontConnectionError, ZontProtocolError
from .const import DOMAIN, connection_signal
from .controller import async_restart_controller
from .entity import ZontEntityMixin
from .runtime import ZontRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZONT buttons."""
    async_add_entities([ZontRestartButton(entry)])


class ZontRestartButton(ZontEntityMixin, ButtonEntity):
    """Restart the physical ZONT controller."""

    _attr_translation_key = "restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the restart button."""
        self._entry = entry
        self._client = entry.runtime_data.client
        self._attr_available = self._client.is_connected
        self._set_zont_identity(entry, "restart")

    async def async_added_to_hass(self) -> None:
        """Subscribe to local connection changes."""
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
        """Update button availability with the local connection."""
        self._attr_available = connected
        self.async_write_ha_state()

    async def async_press(self) -> None:
        """Send the restart command without waiting for a response."""
        try:
            await async_restart_controller(self._client)
        except asyncio.CancelledError:
            raise
        except ZontConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="controller_offline",
            ) from err
        except ZontProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protocol_error",
            ) from err
