"""Diagnostic entities for ZONT heating circuits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature

from ...entity import ZontObjectCoordinatorEntity
from ...protocol.heating_config import (
    CONSUMER_CIRCUIT_SUBTYPE,
    DHW_CIRCUIT_SUBTYPE,
    ZontConsumerControlMode,
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
)
from ...protocol.objects import ZontHeatingCircuitData, ZontHeatingCircuitMode
from ...runtime import ZontRuntimeData

CONSUMER_CONTROL_MODE_STATES = tuple(mode.value for mode in ZontConsumerControlMode)


class ZontHeatingControlModeSensor(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent the configured control mode of a consumer heating circuit."""

    _attr_translation_key = "heating_control_mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = list(CONSUMER_CONTROL_MODE_STATES)

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a consumer-circuit control mode sensor."""
        super().__init__(entry, object_id, "control_mode", "control_mode")

    @property
    def available(self) -> bool:
        """Return whether the circuit control mode has been resolved."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | None:
        """Return the configured base control mode."""
        control = self.coordinator.data.heating_controls.get(self._object_id)
        return control.control_mode.value if control and control.control_mode else None


class ZontHeatingCalculatedWaterTemperatureSensor(
    ZontObjectCoordinatorEntity,
    SensorEntity,
):
    """Represent the controller-calculated heating-water temperature."""

    _attr_translation_key = "heating_calculated_water_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a calculated heating-water temperature sensor."""
        super().__init__(
            entry,
            object_id,
            "calculated_water_temperature",
            "calculated_water_temperature",
        )

    @property
    def available(self) -> bool:
        """Return whether the controller currently provides the calculation."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the calculated or required heating-water temperature."""
        state = self.coordinator.data.heating_states.get(self._object_id)
        return state.calculated_water_temperature if state is not None else None


@dataclass(frozen=True, kw_only=True)
class ZontHeatingCircuitBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe one heating-circuit binary state."""

    value_fn: Callable[
        [
            ZontHeatingCircuitData,
            ZontHeatingCircuitControlData | None,
            ZontHeatingCircuitInternalState | None,
        ],
        bool | None,
    ]


CONSUMER_HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS = (
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="weather_compensation",
        translation_key="heating_weather_compensation",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda circuit, control, state: (
            control.has_weather_compensation if control is not None else None
        ),
    ),
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="blocked",
        translation_key="heating_blocked",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda circuit, control, state: (
            state.is_blocked if state is not None else None
        ),
    ),
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="sensor_fault",
        translation_key="heating_sensor_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda circuit, control, state: (
            state.has_sensor_fault if state is not None else None
        ),
    ),
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="summer_mode",
        translation_key="heating_summer_mode",
        value_fn=lambda circuit, control, state: (
            state.is_summer_mode if state is not None else None
        ),
    ),
)

HEATING_CIRCUIT_FAULT_DESCRIPTION = ZontHeatingCircuitBinarySensorEntityDescription(
    key="fault",
    translation_key="heating_fault",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda circuit, control, state: circuit.fault,
)

HEATING_CIRCUIT_HEATING_DESCRIPTION = ZontHeatingCircuitBinarySensorEntityDescription(
    key="heating",
    translation_key="heating",
    device_class=BinarySensorDeviceClass.RUNNING,
    value_fn=lambda circuit, control, state: (
        False
        if circuit.mode is ZontHeatingCircuitMode.OFF
        else state.is_heating
        if state is not None
        else None
    ),
)

HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE = {
    DHW_CIRCUIT_SUBTYPE: (
        HEATING_CIRCUIT_HEATING_DESCRIPTION,
        HEATING_CIRCUIT_FAULT_DESCRIPTION,
    ),
    CONSUMER_CIRCUIT_SUBTYPE: (
        *CONSUMER_HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS,
        HEATING_CIRCUIT_FAULT_DESCRIPTION,
    ),
}


class ZontHeatingCircuitBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent one binary state of a heating circuit."""

    entity_description: ZontHeatingCircuitBinarySensorEntityDescription

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        description: ZontHeatingCircuitBinarySensorEntityDescription,
    ) -> None:
        """Initialize a heating-circuit binary sensor."""
        self.entity_description = description
        super().__init__(entry, object_id, description.key, description.key)

    @property
    def available(self) -> bool:
        """Return whether the source currently provides this binary state."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the described consumer heating-circuit state."""
        obj = self.object_data
        if not isinstance(obj, ZontHeatingCircuitData):
            return None
        return self.entity_description.value_fn(
            obj,
            self.coordinator.data.heating_controls.get(self._object_id),
            self.coordinator.data.heating_states.get(self._object_id),
        )
