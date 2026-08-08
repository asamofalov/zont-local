"""Sensors for the ZONT integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ZontRuntimeData
from .entity import ZontCoordinatorEntity

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZONT sensors."""
    async_add_entities(
        [
            ZontConnectionChannelSensor(entry),
            ZontSupplyVoltageSensor(entry),
        ]
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
