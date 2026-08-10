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
    REVOLUTIONS_PER_MINUTE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZontRuntimeData
from .entity import ZontCoordinatorEntity, ZontObjectCoordinatorEntity
from .heating_config import CONSUMER_CIRCUIT_SUBTYPE, ZontConsumerControlMode
from .mixer import ZontMixerInternalState
from .object_import import object_import_configuration
from .objects import (
    ANALOG_BINARY_FIRST_SUBTYPES,
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontMixerDirection,
    ZontNtcTemperatureSensorData,
    ZontRadioSensorData,
    ZontTemperatureSensorData,
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
CONSUMER_CONTROL_MODE_STATES = tuple(mode.value for mode in ZontConsumerControlMode)
MIXER_STATE_STATES = (
    "idle",
    "opening",
    "closing",
    "fully_open",
    "fully_closed",
)

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
    import_configuration = object_import_configuration(entry.options)

    @callback
    def async_add_object_entities() -> None:
        """Add entities for newly discovered object fields."""
        new_entities: list[SensorEntity] = []
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if not import_configuration.imports(obj.object_id):
                continue
            if isinstance(obj, ZontAnalogInputData):
                identity = (obj.object_id, "value")
                if identity not in known_entities:
                    known_entities.add(identity)
                    new_entities.append(
                        ZontAnalogInputValueSensor(entry, obj.object_id, obj.subtype)
                    )
                continue
            if isinstance(obj, ZontDigitalTemperatureSensorData):
                identity = (obj.object_id, "temperature")
                if identity not in known_entities:
                    known_entities.add(identity)
                    new_entities.append(
                        ZontDigitalTemperatureSensor(entry, obj.object_id)
                    )
                continue
            if isinstance(obj, ZontNtcTemperatureSensorData):
                identity = (obj.object_id, "temperature")
                if identity not in known_entities:
                    known_entities.add(identity)
                    new_entities.append(ZontNtcTemperatureSensor(entry, obj.object_id))
                continue
            if isinstance(obj, ZontRadioSensorData):
                for field in RADIO_SENSOR_FIELDS_BY_SUBTYPE.get(obj.subtype, ()):
                    identity = (obj.object_id, field)
                    if identity in known_entities:
                        continue
                    known_entities.add(identity)
                    new_entities.append(
                        ZontRadioSensor(
                            entry,
                            obj.object_id,
                            RADIO_SENSOR_DESCRIPTIONS[field],
                        )
                    )
                continue
            if (
                isinstance(obj, ZontHeatingCircuitData)
                and obj.subtype == CONSUMER_CIRCUIT_SUBTYPE
            ):
                identity = (obj.object_id, "control_mode")
                if identity not in known_entities:
                    known_entities.add(identity)
                    new_entities.append(
                        ZontHeatingControlModeSensor(entry, obj.object_id)
                    )
                continue
            if isinstance(obj, ZontMixerData):
                identity = (obj.object_id, "state")
                if identity not in known_entities:
                    known_entities.add(identity)
                    new_entities.append(ZontMixerStateSensor(entry, obj.object_id))
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


class ZontMixerStateSensor(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent the read-only movement or end position of one mixer."""

    _attr_name = None
    _attr_translation_key = "mixer_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MIXER_STATE_STATES)

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a mixer state sensor."""
        super().__init__(entry, object_id, "state", None)

    @property
    def available(self) -> bool:
        """Return whether a current, unambiguous mixer state is known."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | None:
        """Return movement first, otherwise the periodically read position."""
        obj = self.object_data
        if not isinstance(obj, ZontMixerData) or obj.direction is None:
            return None
        if obj.direction is ZontMixerDirection.OPENING:
            return "opening"
        if obj.direction is ZontMixerDirection.CLOSING:
            return "closing"

        state = self.coordinator.data.mixer_states.get(self._object_id)
        return _stopped_mixer_state(state)


def _stopped_mixer_state(state: ZontMixerInternalState | None) -> str | None:
    """Resolve one stopped mixer position without guessing contradictions."""
    if state is None:
        return None
    if state.is_fully_open and state.is_fully_closed:
        return None
    if state.is_fully_open:
        return "fully_open"
    if state.is_fully_closed:
        return "fully_closed"
    return "idle"


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
