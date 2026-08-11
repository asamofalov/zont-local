"""Typed models for ZONT protocol objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

OBJECT_TYPE_ANALOG_INPUT = 0
OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR = 1
OBJECT_TYPE_DIGITAL_BUS_ADAPTER = 6
OBJECT_TYPE_RADIO_SENSOR = 8
OBJECT_TYPE_RELAY = 14
OBJECT_TYPE_MIXER = 15
OBJECT_TYPE_HEATING_CIRCUIT = 16
OBJECT_TYPE_PUMP = 17
OBJECT_TYPE_NTC_TEMPERATURE_SENSOR = 27
ANALOG_INPUT_SUBTYPE_DISCRETE_NO = 19
ANALOG_INPUT_SUBTYPE_DISCRETE_NC = 20
SUPPORTED_OBJECT_TYPES = (
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
    OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
    OBJECT_TYPE_RADIO_SENSOR,
    OBJECT_TYPE_HEATING_CIRCUIT,
    OBJECT_TYPE_PUMP,
    OBJECT_TYPE_NTC_TEMPERATURE_SENSOR,
    OBJECT_TYPE_MIXER,
    OBJECT_TYPE_RELAY,
)

ANALOG_BINARY_FIRST_SUBTYPES = frozenset({3, 4, 5, 6, 7, 9, 10, 11, 14, 15, 19, 20})


class ZontObjectParseError(ValueError):
    """Raised when a ZONT object payload has no usable identity."""


class ZontDigitalBusState(StrEnum):
    """Documented operating states of a digital bus adapter."""

    OFF = "off"
    RUNNING = "running"
    ERROR = "error"


class ZontHeatingCircuitMode(StrEnum):
    """Documented observed modes of a heating circuit."""

    HEAT = "heat"
    COOL = "cool"
    OFF = "off"


class ZontMixerDirection(StrEnum):
    """Documented movement states of a mixer."""

    IDLE = "idle"
    OPENING = "opening"
    CLOSING = "closing"


@dataclass(frozen=True, slots=True)
class ZontObjectData:
    """Common descriptive data for one ZONT object."""

    object_id: int
    object_type: int
    name: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class ZontAnalogInputData(ZontObjectData):
    """Read-only state and configuration of one analog input."""

    subtype: int = 0
    value: float | None = None
    unit_code: int | None = None
    triggered: bool | None = None


@dataclass(frozen=True, slots=True)
class ZontDigitalBusAdapterData(ZontObjectData):
    """Read-only state of a boiler digital bus adapter."""

    flow_temperature: float | None = None
    dhw_temperature: float | None = None
    return_temperature: float | None = None
    modulation: float | None = None
    pressure: float | None = None
    state: ZontDigitalBusState | None = None
    error_code: int | None = None

    @property
    def has_fault(self) -> bool | None:
        """Return whether the adapter reports an explicit boiler fault."""
        if self.state is ZontDigitalBusState.ERROR or (
            self.error_code is not None and self.error_code != 0
        ):
            return True
        if self.state is not None or self.error_code is not None:
            return False
        return None


@dataclass(frozen=True, slots=True)
class ZontTemperatureSensorData(ZontObjectData):
    """Common read-only state of a temperature sensor."""

    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class ZontDigitalTemperatureSensorData(ZontTemperatureSensorData):
    """Read-only state of a digital temperature sensor."""


@dataclass(frozen=True, slots=True)
class ZontNtcTemperatureSensorData(ZontTemperatureSensorData):
    """Read-only state of an NTC temperature sensor."""


@dataclass(frozen=True, slots=True)
class ZontRadioSensorData(ZontObjectData):
    """Read-only state of one radio sensor."""

    subtype: int = 0
    temperature: float | None = None
    humidity: float | None = None
    battery_voltage: float | None = None
    signal_strength_raw: float | None = None
    triggered: bool | None = None


@dataclass(frozen=True, slots=True)
class ZontHeatingCircuitData(ZontObjectData):
    """Observed state and setpoint of one heating circuit."""

    subtype: int = 0
    current_temperature: float | None = None
    target_temperature: float | None = None
    mode: ZontHeatingCircuitMode | None = None
    mode_id: int | None = None
    fault: bool | None = None


@dataclass(frozen=True, slots=True)
class ZontPumpData(ZontObjectData):
    """Observed state of one pump."""

    running: bool | None = None


@dataclass(frozen=True, slots=True)
class ZontMixerData(ZontObjectData):
    """Observed movement state of one mixer."""

    direction: ZontMixerDirection | None = None


@dataclass(frozen=True, slots=True)
class ZontRelayData(ZontObjectData):
    """Observed physical output state of one relay."""

    output_active: bool | None = None


type ZontObject = (
    ZontAnalogInputData
    | ZontDigitalBusAdapterData
    | ZontDigitalTemperatureSensorData
    | ZontNtcTemperatureSensorData
    | ZontRadioSensorData
    | ZontHeatingCircuitData
    | ZontPumpData
    | ZontMixerData
    | ZontRelayData
)


def immutable_objects(
    objects: Mapping[int, ZontObject] | None = None,
) -> Mapping[int, ZontObject]:
    """Return an immutable copy of the object registry."""
    return MappingProxyType(dict(objects or {}))


def unavailable_object(obj: ZontObject) -> ZontObject:
    """Return an object snapshot marked unavailable without losing its data."""
    return replace(obj, available=False)
