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
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from ..const import DOMAIN, connection_signal
from ..entity import ZontCoordinatorEntity, ZontEntityMixin
from ..protocol import ZontConnectionError, ZontProtocolError
from ..protocol.controller import (
    ZontGsmRegistrationState,
    ZontPowerSource,
    async_restart_controller,
)
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
GSM_REGISTRATION_STATES = tuple(state.value for state in ZontGsmRegistrationState)
POWER_SOURCE_STATES = tuple(source.value for source in ZontPowerSource)


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


class ZontPowerSourceSensor(ZontCoordinatorEntity, SensorEntity):
    """Represent the controller power source."""

    _attr_translation_key = "power_source"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = list(POWER_SOURCE_STATES)

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the power source sensor."""
        super().__init__(entry, "power_source")

    @property
    def available(self) -> bool:
        """Return whether the controller reported its power source."""
        return super().available and self.controller_data.power_source is not None

    @property
    def native_value(self) -> str | None:
        """Return the current power source."""
        source = self.controller_data.power_source
        return source.value if source is not None else None


class ZontGsmRegistrationSensor(ZontCoordinatorEntity, SensorEntity):
    """Represent GSM network registration."""

    _attr_translation_key = "gsm_registration"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = list(GSM_REGISTRATION_STATES)

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the GSM registration sensor."""
        super().__init__(entry, "gsm_registration")

    @property
    def available(self) -> bool:
        """Return whether GSM state has been obtained."""
        return super().available and self.controller_data.gsm_status is not None

    @property
    def native_value(self) -> str | None:
        """Return the GSM registration state."""
        status = self.controller_data.gsm_status
        return status.registration.value if status is not None else None


class ZontWifiSignalSensor(ZontCoordinatorEntity, SensorEntity):
    """Represent the normalized Wi-Fi signal level."""

    _attr_translation_key = "wifi_signal"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the Wi-Fi signal sensor."""
        super().__init__(entry, "wifi_signal")

    @property
    def available(self) -> bool:
        """Return whether Wi-Fi state has been obtained."""
        return super().available and self.controller_data.wifi_status is not None

    @property
    def native_value(self) -> int | None:
        """Return the normalized Wi-Fi signal level."""
        status = self.controller_data.wifi_status
        return status.signal_percent if status is not None else None


class ZontGsmSignalSensor(ZontCoordinatorEntity, SensorEntity):
    """Represent the normalized GSM signal level."""

    _attr_translation_key = "gsm_signal"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the GSM signal sensor."""
        super().__init__(entry, "gsm_signal")

    @property
    def available(self) -> bool:
        """Return whether a supported GSM level has been obtained."""
        status = self.controller_data.gsm_status
        return (
            super().available
            and status is not None
            and status.signal_percent is not None
        )

    @property
    def native_value(self) -> int | None:
        """Return the normalized GSM signal level."""
        status = self.controller_data.gsm_status
        return status.signal_percent if status is not None else None


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


class ZontWifiConnectedBinarySensor(ZontCoordinatorEntity, BinarySensorEntity):
    """Represent the controller Wi-Fi link state."""

    _attr_translation_key = "wifi_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the Wi-Fi connection sensor."""
        super().__init__(entry, "wifi_connected")

    @property
    def available(self) -> bool:
        """Return whether Wi-Fi state has been obtained."""
        return super().available and self.controller_data.wifi_status is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether Wi-Fi is connected to an access point."""
        status = self.controller_data.wifi_status
        return status.connected if status is not None else None


class ZontEthernetConnectedBinarySensor(ZontCoordinatorEntity, BinarySensorEntity):
    """Represent the controller Ethernet link state."""

    _attr_translation_key = "ethernet_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the Ethernet connection sensor."""
        super().__init__(entry, "ethernet_connected")

    @property
    def available(self) -> bool:
        """Return whether Ethernet state has been obtained."""
        return super().available and self.controller_data.ethernet_status is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether Ethernet is connected."""
        status = self.controller_data.ethernet_status
        return status.connected if status is not None else None


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
