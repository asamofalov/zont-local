"""Entities for wired ZONT temperature sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature

from ..entity import ZontObjectCoordinatorEntity
from ..protocol.objects import ZontTemperatureSensorData
from ..runtime import ZontRuntimeData


class _ZontTemperatureSensorEntity(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent one ZONT temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a temperature sensor."""
        super().__init__(entry, object_id, "temperature", "temperature")

    @property
    def available(self) -> bool:
        """Return whether the object currently reports a valid temperature."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the current temperature in degrees Celsius."""
        obj = self.object_data
        return obj.temperature if isinstance(obj, ZontTemperatureSensorData) else None


class ZontDigitalTemperatureSensor(_ZontTemperatureSensorEntity):
    """Represent one ZONT digital temperature sensor."""

    _attr_translation_key = "digital_temperature"


class ZontNtcTemperatureSensor(_ZontTemperatureSensorEntity):
    """Represent one ZONT NTC temperature sensor."""

    _attr_translation_key = "ntc_temperature"
