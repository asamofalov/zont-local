"""Entities for ZONT analog inputs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)

from ..entity import ZontObjectCoordinatorEntity
from ..protocol.objects import (
    ANALOG_BINARY_FIRST_SUBTYPES,
    ZontAnalogInputData,
)
from ..runtime import ZontRuntimeData

ANALOG_INPUT_UNITS: dict[int, str | None] = {
    0: UnitOfElectricPotential.VOLT,
    1: "kΩ",
    2: UnitOfPressure.BAR,
    3: UnitOfSpeed.KILOMETERS_PER_HOUR,
    4: REVOLUTIONS_PER_MINUTE,
    5: UnitOfVolume.LITERS,
    6: UnitOfVolumeFlowRate.LITERS_PER_HOUR,
    7: PERCENTAGE,
    8: None,
}

ANALOG_INPUT_DEVICE_CLASSES = {
    0: SensorDeviceClass.VOLTAGE,
    2: SensorDeviceClass.PRESSURE,
    3: SensorDeviceClass.SPEED,
    5: SensorDeviceClass.VOLUME,
    6: SensorDeviceClass.VOLUME_FLOW_RATE,
}


class ZontAnalogInputValueSensor(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent the numeric value reported by one analog input."""

    _attr_translation_key = "analog_value"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        subtype: int,
    ) -> None:
        """Initialize an analog input value sensor."""
        self._subtype = subtype
        if subtype in ANALOG_BINARY_FIRST_SUBTYPES:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False
        super().__init__(entry, object_id, "value", "value")

    @property
    def available(self) -> bool:
        """Return whether the input currently reports a valid value."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the current analog input value."""
        obj = self.object_data
        return obj.value if isinstance(obj, ZontAnalogInputData) else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit documented by the controller."""
        obj = self.object_data
        if not isinstance(obj, ZontAnalogInputData) or obj.unit_code is None:
            return None
        return ANALOG_INPUT_UNITS.get(obj.unit_code)

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return a compatible Home Assistant device class for the unit."""
        obj = self.object_data
        if not isinstance(obj, ZontAnalogInputData) or obj.unit_code is None:
            return None
        if obj.unit_code == 7 and self._subtype == 17:
            return SensorDeviceClass.HUMIDITY
        return ANALOG_INPUT_DEVICE_CLASSES.get(obj.unit_code)


ANALOG_TRIGGER_DEVICE_CLASSES: dict[int, BinarySensorDeviceClass | None] = {
    3: BinarySensorDeviceClass.DOOR,
    4: BinarySensorDeviceClass.MOTION,
    5: BinarySensorDeviceClass.SMOKE,
    6: BinarySensorDeviceClass.MOISTURE,
    7: BinarySensorDeviceClass.MOTION,
    9: BinarySensorDeviceClass.PROBLEM,
    10: BinarySensorDeviceClass.PROBLEM,
    11: BinarySensorDeviceClass.POWER,
    14: None,
    15: BinarySensorDeviceClass.SAFETY,
    19: None,
    20: None,
}


class ZontAnalogInputTriggeredBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the trigger flag of one analog input."""

    _attr_translation_key = "analog_triggered"

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        subtype: int,
    ) -> None:
        """Initialize an analog input trigger sensor."""
        self._attr_device_class = ANALOG_TRIGGER_DEVICE_CLASSES.get(
            subtype,
            BinarySensorDeviceClass.PROBLEM,
        )
        super().__init__(entry, object_id, "triggered", "triggered")

    @property
    def available(self) -> bool:
        """Return whether the input currently reports a valid trigger flag."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the current analog input trigger flag."""
        obj = self.object_data
        return obj.triggered if isinstance(obj, ZontAnalogInputData) else None
