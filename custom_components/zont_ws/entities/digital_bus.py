"""Sensor entities for ZONT digital bus adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
)

from ..entity import ZontObjectCoordinatorEntity
from ..protocol.objects import ZontDigitalBusAdapterData, ZontDigitalBusState
from ..runtime import ZontRuntimeData

DIGITAL_BUS_STATES = tuple(state.value for state in ZontDigitalBusState)


@dataclass(frozen=True, kw_only=True)
class ZontDigitalBusSensorEntityDescription(SensorEntityDescription):
    """Describe one readable digital bus adapter field."""

    value_fn: Callable[[ZontDigitalBusAdapterData], Any]


DIGITAL_BUS_SENSOR_DESCRIPTIONS = (
    ZontDigitalBusSensorEntityDescription(
        key="flow_temperature",
        translation_key="digital_bus_flow_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda adapter: adapter.flow_temperature,
    ),
    ZontDigitalBusSensorEntityDescription(
        key="dhw_temperature",
        translation_key="digital_bus_dhw_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda adapter: adapter.dhw_temperature,
    ),
    ZontDigitalBusSensorEntityDescription(
        key="return_temperature",
        translation_key="digital_bus_return_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda adapter: adapter.return_temperature,
    ),
    ZontDigitalBusSensorEntityDescription(
        key="modulation",
        translation_key="digital_bus_modulation",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda adapter: adapter.modulation,
    ),
    ZontDigitalBusSensorEntityDescription(
        key="pressure",
        translation_key="digital_bus_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda adapter: adapter.pressure,
    ),
    ZontDigitalBusSensorEntityDescription(
        key="state",
        translation_key="digital_bus_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(DIGITAL_BUS_STATES),
        value_fn=lambda adapter: adapter.state,
    ),
    ZontDigitalBusSensorEntityDescription(
        key="error_code",
        translation_key="digital_bus_error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda adapter: adapter.error_code,
    ),
)


class ZontDigitalBusFaultBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent an explicit fault reported by a digital bus adapter."""

    _attr_translation_key = "digital_bus_fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a digital bus adapter fault sensor."""
        super().__init__(entry, object_id, "fault", "fault")

    @property
    def available(self) -> bool:
        """Return whether the adapter provides a fault source."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the adapter state or error code reports a fault."""
        obj = self.object_data
        return obj.has_fault if isinstance(obj, ZontDigitalBusAdapterData) else None


class ZontDigitalBusSensor(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent one value reported by a digital bus adapter."""

    entity_description: ZontDigitalBusSensorEntityDescription

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        description: ZontDigitalBusSensorEntityDescription,
    ) -> None:
        """Initialize a digital bus adapter sensor."""
        self.entity_description = description
        super().__init__(entry, object_id, description.key, description.key)

    @property
    def available(self) -> bool:
        """Return whether this specific field is currently reported."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> Any:
        """Return the current value of the described adapter field."""
        obj = self.object_data
        return (
            self.entity_description.value_fn(obj)
            if isinstance(obj, ZontDigitalBusAdapterData)
            else None
        )
