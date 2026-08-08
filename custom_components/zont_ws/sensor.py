"""Sensors for the ZONT integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZontRuntimeData
from .entity import ZontCoordinatorEntity, ZontObjectCoordinatorEntity
from .objects import (
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
)

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT sensors."""
    async_add_entities(
        [
            ZontConnectionChannelSensor(entry),
            ZontSupplyVoltageSensor(entry),
        ]
    )

    known_entities: set[tuple[int, str]] = set()

    @callback
    def async_add_object_entities() -> None:
        """Add entities for newly discovered object fields."""
        new_entities: list[SensorEntity] = []
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if isinstance(obj, ZontDigitalTemperatureSensorData):
                identity = (obj.object_id, "temperature")
                if identity not in known_entities:
                    known_entities.add(identity)
                    new_entities.append(
                        ZontDigitalTemperatureSensor(entry, obj.object_id)
                    )
                continue
            if isinstance(obj, ZontDigitalBusAdapterData) and obj.available:
                for description in DIGITAL_BUS_SENSOR_DESCRIPTIONS:
                    identity = (obj.object_id, description.key)
                    if identity in known_entities or description.value_fn(obj) is None:
                        continue
                    known_entities.add(identity)
                    new_entities.append(
                        ZontDigitalBusSensor(entry, obj.object_id, description)
                    )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        entry.runtime_data.coordinator.async_add_listener(async_add_object_entities)
    )
    async_add_object_entities()


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


class ZontDigitalTemperatureSensor(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent one ZONT digital temperature sensor."""

    _attr_translation_key = "digital_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a digital temperature sensor."""
        super().__init__(entry, object_id, "temperature", "temperature")

    @property
    def available(self) -> bool:
        """Return whether the object currently reports a valid temperature."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the current temperature in degrees Celsius."""
        obj = self.object_data
        return (
            obj.temperature
            if isinstance(obj, ZontDigitalTemperatureSensorData)
            else None
        )
