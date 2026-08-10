"""Entities attached directly to the ZONT controller."""

from __future__ import annotations

import asyncio

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricPotential
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from ..const import DOMAIN, connection_signal
from ..entity import ZontCoordinatorEntity, ZontEntityMixin
from ..protocol import ZontConnectionError, ZontProtocolError
from ..protocol.controller import async_restart_controller
from ..runtime import ZontRuntimeData

CONNECTION_CHANNEL_STATES = (
    "none",
    "gsm",
    "wifi",
    "ethernet",
    "gsm_wifi",
    "gsm_ethernet",
    "wifi_ethernet",
    "gsm_wifi_ethernet",
)


class ZontConnectionChannelSensor(ZontCoordinatorEntity, SensorEntity):
    """Represent active controller communication channels."""

    _attr_translation_key = "connection_channel"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = list(CONNECTION_CHANNEL_STATES)

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the communication channel sensor."""
        super().__init__(entry, "connection_channel")

    @property
    def available(self) -> bool:
        """Return whether server status has been obtained."""
        return super().available and self.controller_data.server_status is not None

    @property
    def native_value(self) -> str | None:
        """Return a stable enum value for all active channels."""
        status = self.controller_data.server_status
        return status.channel_state if status is not None else None


class ZontSupplyVoltageSensor(ZontCoordinatorEntity, SensorEntity):
    """Represent controller supply voltage."""

    _attr_translation_key = "supply_voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the supply voltage sensor."""
        super().__init__(entry, "supply_voltage")

    @property
    def available(self) -> bool:
        """Return whether supply voltage has been obtained."""
        return super().available and self.controller_data.supply_voltage is not None

    @property
    def native_value(self) -> float | None:
        """Return the current supply voltage in volts."""
        return self.controller_data.supply_voltage


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
