"""Entities for ZONT radio sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)

from ..entity import ZontObjectCoordinatorEntity
from ..protocol.objects import ZontRadioSensorData
from ..runtime import ZontRuntimeData


@dataclass(frozen=True, kw_only=True)
class ZontRadioSensorEntityDescription(SensorEntityDescription):
    """Describe one readable radio sensor field."""

    value_fn: Callable[[ZontRadioSensorData], float | None]


RADIO_SENSOR_DESCRIPTIONS = {
    "temperature": ZontRadioSensorEntityDescription(
        key="temperature",
        translation_key="radio_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda sensor: sensor.temperature,
    ),
    "humidity": ZontRadioSensorEntityDescription(
        key="humidity",
        translation_key="radio_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda sensor: sensor.humidity,
    ),
    "battery_voltage": ZontRadioSensorEntityDescription(
        key="battery_voltage",
        translation_key="radio_battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda sensor: sensor.battery_voltage,
    ),
    "signal_strength": ZontRadioSensorEntityDescription(
        key="signal_strength",
        translation_key="radio_signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda sensor: (
            sensor.signal_strength_raw / 2 - 73
            if sensor.signal_strength_raw is not None
            else None
        ),
    ),
}

RADIO_SENSOR_FIELDS_BY_SUBTYPE = {
    5: ("temperature", "battery_voltage", "signal_strength"),
    10: ("battery_voltage", "signal_strength"),
    11: ("battery_voltage", "signal_strength"),
    15: ("temperature", "battery_voltage", "signal_strength"),
    18: ("temperature", "humidity", "battery_voltage", "signal_strength"),
}


class ZontRadioSensor(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent one numeric value reported by a radio sensor."""

    entity_description: ZontRadioSensorEntityDescription

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        description: ZontRadioSensorEntityDescription,
    ) -> None:
        """Initialize a radio sensor entity."""
        self.entity_description = description
        super().__init__(entry, object_id, description.key, description.key)

    @property
    def available(self) -> bool:
        """Return whether this specific radio field is currently reported."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the current value of the described radio sensor field."""
        obj = self.object_data
        return (
            self.entity_description.value_fn(obj)
            if isinstance(obj, ZontRadioSensorData)
            else None
        )


RADIO_TRIGGER_DEVICE_CLASSES = {
    10: BinarySensorDeviceClass.MOISTURE,
    11: BinarySensorDeviceClass.MOTION,
}


class ZontRadioTriggeredBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the trigger flag of one radio sensor."""

    _attr_translation_key = "radio_triggered"

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        subtype: int,
    ) -> None:
        """Initialize a radio sensor trigger entity."""
        self._attr_device_class = RADIO_TRIGGER_DEVICE_CLASSES[subtype]
        super().__init__(entry, object_id, "triggered", "triggered")

    @property
    def available(self) -> bool:
        """Return whether the radio sensor reports a valid trigger flag."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the current radio sensor trigger flag."""
        obj = self.object_data
        return obj.triggered if isinstance(obj, ZontRadioSensorData) else None
