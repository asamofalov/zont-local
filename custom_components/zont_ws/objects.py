"""Typed ZONT object models and protocol parsers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any

OBJECT_TYPE_ANALOG_INPUT = 0
OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR = 1
OBJECT_TYPE_DIGITAL_BUS_ADAPTER = 6
OBJECT_TYPE_RADIO_SENSOR = 8
OBJECT_TYPE_HEATING_CIRCUIT = 16
OBJECT_TYPE_NTC_TEMPERATURE_SENSOR = 27
SUPPORTED_OBJECT_TYPES = (
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
    OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
    OBJECT_TYPE_RADIO_SENSOR,
    OBJECT_TYPE_HEATING_CIRCUIT,
    OBJECT_TYPE_NTC_TEMPERATURE_SENSOR,
)

HEATING_CIRCUIT_SUBTYPE_NAMES = MappingProxyType(
    {
        0: "Котловой контур",
        1: "Контур ГВС",
        2: "Охладительный контур",
        3: "Контур потребителя",
    }
)

ANALOG_INPUT_SUBTYPE_NAMES = MappingProxyType(
    {
        0: "Аналоговый вход без пресета",
        1: "Датчик давления 5 бар",
        2: "Датчик давления 12 бар",
        3: "Датчик открытия двери",
        4: "ИК-датчик движения с контролем шлейфа",
        5: "Датчик дыма",
        6: "Датчик протечки",
        7: "ИК-датчик движения без контроля шлейфа",
        8: "Комнатный термостат",
        9: "Авария котла +",
        10: "Авария котла -",
        11: "Вход «Зажигание»",
        12: "Датчик скорости",
        13: "Датчик оборотов двигателя",
        14: "Дискретный вход",
        15: "Тревожная кнопка",
        16: "Датчик расхода топлива",
        17: "Датчик влажности",
        18: "Датчик давления 6 бар",
        19: "Дискретный вход НР",
        20: "Дискретный вход НЗ",
        21: "Датчик давления 10 бар",
    }
)

ANALOG_BINARY_FIRST_SUBTYPES = frozenset({3, 4, 5, 6, 7, 9, 10, 11, 14, 15, 19, 20})

RADIO_SENSOR_SUBTYPE_NAMES = MappingProxyType(
    {
        2: "Трёхкнопочный брелок",
        3: "Четырёхкнопочный брелок",
        4: "Радиореле блокировки",
        5: "Радиотермометр",
        6: "Радиодут",
        7: "Модуль капота",
        8: "Радиометка",
        10: "Радиодатчик протечки",
        11: "Радиодатчик движения",
        12: "Радиодатчик удара",
        13: "Радиотермометр из трёх датчиков и концевого контакта",
        14: "Радиодатчик расхода электроэнергии",
        15: "Внешний радиодатчик температуры",
        16: "Радиодатчик расхода воды и газа",
        17: "Радиорозетка 220 В",
        18: "Радиодатчик температуры и влажности",
        19: "Радиометка с акселерометром",
        20: "Радиометка с аккумулятором",
        23: "Радиопанель или радиотермостат",
    }
)

SUPPORTED_RADIO_SENSOR_SUBTYPES = frozenset({5, 10, 11, 15, 18})


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


type ZontObject = (
    ZontAnalogInputData
    | ZontDigitalBusAdapterData
    | ZontDigitalTemperatureSensorData
    | ZontNtcTemperatureSensorData
    | ZontRadioSensorData
    | ZontHeatingCircuitData
)


def analog_input_model(subtype: int) -> str:
    """Return the documented display name for an analog input subtype."""
    return ANALOG_INPUT_SUBTYPE_NAMES.get(
        subtype,
        f"Аналоговый вход (подтип {subtype})",
    )


def radio_sensor_model(subtype: int) -> str:
    """Return the documented display name for a radio sensor subtype."""
    return RADIO_SENSOR_SUBTYPE_NAMES.get(
        subtype,
        f"Радиодатчик (подтип {subtype})",
    )


def heating_circuit_model(subtype: int) -> str:
    """Return the documented display name for a heating circuit subtype."""
    return HEATING_CIRCUIT_SUBTYPE_NAMES.get(
        subtype,
        f"Контур отопления (подтип {subtype})",
    )


def object_device_identifier(controller_identifier: str, object_id: int) -> str:
    """Return a stable device registry identifier for one controller object."""
    return f"{controller_identifier}:object:{object_id}"


def immutable_objects(
    objects: Mapping[int, ZontObject] | None = None,
) -> Mapping[int, ZontObject]:
    """Return an immutable copy of the object registry."""
    return MappingProxyType(dict(objects or {}))


def unavailable_object(obj: ZontObject) -> ZontObject:
    """Return an object snapshot marked unavailable without losing its data."""
    return replace(obj, available=False)


def parse_analog_input(
    payload: Mapping[str, Any],
    previous: ZontAnalogInputData | None = None,
    *,
    partial: bool = False,
) -> ZontAnalogInputData:
    """Parse a full or partial analog input payload."""
    object_id = _identity_int(payload, "id", previous.object_id if previous else None)
    object_type = _identity_int(
        payload,
        "type",
        previous.object_type if previous else None,
    )
    if object_type != OBJECT_TYPE_ANALOG_INPUT:
        raise ZontObjectParseError("Object is not an analog input")

    name = payload.get("name", previous.name if previous else None)
    if not isinstance(name, str) or not name.strip():
        raise ZontObjectParseError("Object name is missing")

    subtype = _identity_int(
        payload,
        "stype",
        previous.subtype if previous else None,
    )
    value = _optional_number(
        payload,
        "v",
        previous.value if previous is not None else None,
        partial,
    )
    triggered = _optional_binary_state(
        payload,
        "trig",
        previous.triggered if previous is not None else None,
        partial,
    )
    unit_code = _optional_non_negative_int(
        payload,
        "u",
        previous.unit_code if previous is not None else None,
        partial,
    )
    available = _object_available(
        payload,
        previous.available if previous is not None else None,
        partial,
        value is not None or triggered is not None,
    )
    if not available and previous is not None:
        if value is None:
            value = previous.value
        if triggered is None:
            triggered = previous.triggered
        if unit_code is None:
            unit_code = previous.unit_code

    return ZontAnalogInputData(
        object_id=object_id,
        object_type=object_type,
        name=name.strip(),
        available=available,
        subtype=subtype,
        value=value,
        unit_code=unit_code,
        triggered=triggered,
    )


def parse_digital_bus_adapter(
    payload: Mapping[str, Any],
    previous: ZontDigitalBusAdapterData | None = None,
    *,
    partial: bool = False,
) -> ZontDigitalBusAdapterData:
    """Parse a full or partial digital bus adapter payload."""
    object_id = _identity_int(payload, "id", previous.object_id if previous else None)
    object_type = _identity_int(
        payload,
        "type",
        previous.object_type if previous else None,
    )
    if object_type != OBJECT_TYPE_DIGITAL_BUS_ADAPTER:
        raise ZontObjectParseError("Object is not a digital bus adapter")

    name = payload.get("name", previous.name if previous else None)
    if not isinstance(name, str) or not name.strip():
        raise ZontObjectParseError("Object name is missing")

    return ZontDigitalBusAdapterData(
        object_id=object_id,
        object_type=object_type,
        name=name.strip(),
        available=True,
        flow_temperature=_number_field(payload, "water", previous, partial),
        dhw_temperature=_number_field(payload, "dhw", previous, partial),
        return_temperature=_number_field(payload, "return", previous, partial),
        modulation=_number_field(payload, "modul", previous, partial),
        pressure=_number_field(payload, "press", previous, partial),
        state=_state_field(payload, previous, partial),
        error_code=_integer_field(payload, "err", previous, partial),
    )


def parse_digital_temperature_sensor(
    payload: Mapping[str, Any],
    previous: ZontDigitalTemperatureSensorData | None = None,
    *,
    partial: bool = False,
) -> ZontDigitalTemperatureSensorData:
    """Parse a full or partial digital temperature sensor payload."""
    return _parse_temperature_sensor(
        payload,
        previous,
        partial=partial,
        expected_object_type=OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
        data_class=ZontDigitalTemperatureSensorData,
    )


def parse_ntc_temperature_sensor(
    payload: Mapping[str, Any],
    previous: ZontNtcTemperatureSensorData | None = None,
    *,
    partial: bool = False,
) -> ZontNtcTemperatureSensorData:
    """Parse a full or partial NTC temperature sensor payload."""
    return _parse_temperature_sensor(
        payload,
        previous,
        partial=partial,
        expected_object_type=OBJECT_TYPE_NTC_TEMPERATURE_SENSOR,
        data_class=ZontNtcTemperatureSensorData,
    )


def parse_radio_sensor(
    payload: Mapping[str, Any],
    previous: ZontRadioSensorData | None = None,
    *,
    partial: bool = False,
) -> ZontRadioSensorData:
    """Parse a full or partial radio sensor payload."""
    object_id = _identity_int(payload, "id", previous.object_id if previous else None)
    object_type = _identity_int(
        payload,
        "type",
        previous.object_type if previous else None,
    )
    if object_type != OBJECT_TYPE_RADIO_SENSOR:
        raise ZontObjectParseError("Object is not a radio sensor")

    name = payload.get("name", previous.name if previous else None)
    if not isinstance(name, str) or not name.strip():
        raise ZontObjectParseError("Object name is missing")

    subtype = _identity_int(
        payload,
        "stype",
        previous.subtype if previous else None,
    )
    temperature = _optional_number(
        payload,
        "t",
        previous.temperature if previous is not None else None,
        partial,
    )
    humidity = _optional_number(
        payload,
        "h",
        previous.humidity if previous is not None else None,
        partial,
    )
    battery_voltage = _optional_number(
        payload,
        "b",
        previous.battery_voltage if previous is not None else None,
        partial,
    )
    signal_strength_raw = _optional_number(
        payload,
        "r",
        previous.signal_strength_raw if previous is not None else None,
        partial,
    )
    triggered = _optional_binary_state(
        payload,
        "trig",
        previous.triggered if previous is not None else None,
        partial,
    )
    available = _object_available(
        payload,
        previous.available if previous is not None else None,
        partial,
        any(
            value is not None
            for value in (
                temperature,
                humidity,
                battery_voltage,
                signal_strength_raw,
                triggered,
            )
        ),
    )
    if not available and previous is not None:
        temperature = previous.temperature
        humidity = previous.humidity
        battery_voltage = previous.battery_voltage
        signal_strength_raw = previous.signal_strength_raw
        triggered = previous.triggered

    return ZontRadioSensorData(
        object_id=object_id,
        object_type=object_type,
        name=name.strip(),
        available=available,
        subtype=subtype,
        temperature=temperature,
        humidity=humidity,
        battery_voltage=battery_voltage,
        signal_strength_raw=signal_strength_raw,
        triggered=triggered,
    )


def parse_heating_circuit(
    payload: Mapping[str, Any],
    previous: ZontHeatingCircuitData | None = None,
    *,
    partial: bool = False,
) -> ZontHeatingCircuitData:
    """Parse a full or partial heating circuit payload."""
    object_id = _identity_int(payload, "id", previous.object_id if previous else None)
    object_type = _identity_int(
        payload,
        "type",
        previous.object_type if previous else None,
    )
    if object_type != OBJECT_TYPE_HEATING_CIRCUIT:
        raise ZontObjectParseError("Object is not a heating circuit")

    name = payload.get("name", previous.name if previous else None)
    if not isinstance(name, str) or not name.strip():
        raise ZontObjectParseError("Object name is missing")

    subtype = _identity_int(
        payload,
        "stype",
        previous.subtype if previous else None,
    )
    current_temperature = _optional_number(
        payload,
        "c",
        previous.current_temperature if previous is not None else None,
        partial,
    )
    target_temperature = _optional_number(
        payload,
        "s",
        previous.target_temperature if previous is not None else None,
        partial,
    )
    mode = _optional_heating_circuit_mode(
        payload,
        previous.mode if previous is not None else None,
        partial,
    )
    if (
        partial
        and "m" in payload
        and "s" not in payload
        and mode is ZontHeatingCircuitMode.OFF
    ):
        target_temperature = None
    mode_id = _optional_non_negative_int(
        payload,
        "m_id",
        previous.mode_id if previous is not None else None,
        partial,
    )
    fault = _optional_binary_state(
        payload,
        "f",
        previous.fault if previous is not None else None,
        partial,
    )
    available = _object_available(
        payload,
        previous.available if previous is not None else None,
        partial,
        any(
            value is not None
            for value in (
                current_temperature,
                target_temperature,
                mode,
                mode_id,
                fault,
            )
        ),
    )
    if not available and previous is not None:
        current_temperature = previous.current_temperature
        target_temperature = previous.target_temperature
        mode = previous.mode
        mode_id = previous.mode_id
        fault = previous.fault

    return ZontHeatingCircuitData(
        object_id=object_id,
        object_type=object_type,
        name=name.strip(),
        available=available,
        subtype=subtype,
        current_temperature=current_temperature,
        target_temperature=target_temperature,
        mode=mode,
        mode_id=mode_id,
        fault=fault,
    )


def _parse_temperature_sensor[T: ZontTemperatureSensorData](
    payload: Mapping[str, Any],
    previous: T | None,
    *,
    partial: bool,
    expected_object_type: int,
    data_class: type[T],
) -> T:
    """Parse fields common to documented temperature sensor types."""
    object_id = _identity_int(payload, "id", previous.object_id if previous else None)
    object_type = _identity_int(
        payload,
        "type",
        previous.object_type if previous else None,
    )
    if object_type != expected_object_type:
        raise ZontObjectParseError("Object is not the expected temperature sensor")

    name = payload.get("name", previous.name if previous else None)
    if not isinstance(name, str) or not name.strip():
        raise ZontObjectParseError("Object name is missing")

    temperature = _optional_number(
        payload,
        "t",
        previous.temperature if previous is not None else None,
        partial,
    )
    available = _object_available(
        payload,
        previous.available if previous is not None else None,
        partial,
        temperature is not None,
    )
    if not available and temperature is None and previous is not None:
        temperature = previous.temperature

    return data_class(
        object_id=object_id,
        object_type=object_type,
        name=name.strip(),
        available=available,
        temperature=temperature,
    )


def parse_zont_object(
    payload: Mapping[str, Any],
    previous: ZontObject | None = None,
    *,
    partial: bool = False,
) -> ZontObject:
    """Dispatch one object payload to its supported typed parser."""
    object_type = payload.get("type")
    if object_type is None and previous is not None:
        object_type = previous.object_type

    if object_type == OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR:
        previous_sensor = (
            previous if isinstance(previous, ZontDigitalTemperatureSensorData) else None
        )
        return parse_digital_temperature_sensor(
            payload,
            previous_sensor,
            partial=partial,
        )
    if object_type == OBJECT_TYPE_NTC_TEMPERATURE_SENSOR:
        previous_sensor = (
            previous if isinstance(previous, ZontNtcTemperatureSensorData) else None
        )
        return parse_ntc_temperature_sensor(
            payload,
            previous_sensor,
            partial=partial,
        )
    if object_type == OBJECT_TYPE_ANALOG_INPUT:
        previous_input = previous if isinstance(previous, ZontAnalogInputData) else None
        return parse_analog_input(
            payload,
            previous_input,
            partial=partial,
        )
    if object_type == OBJECT_TYPE_RADIO_SENSOR:
        previous_sensor = (
            previous if isinstance(previous, ZontRadioSensorData) else None
        )
        return parse_radio_sensor(
            payload,
            previous_sensor,
            partial=partial,
        )
    if object_type == OBJECT_TYPE_DIGITAL_BUS_ADAPTER:
        previous_adapter = (
            previous if isinstance(previous, ZontDigitalBusAdapterData) else None
        )
        return parse_digital_bus_adapter(
            payload,
            previous_adapter,
            partial=partial,
        )
    if object_type == OBJECT_TYPE_HEATING_CIRCUIT:
        previous_circuit = (
            previous if isinstance(previous, ZontHeatingCircuitData) else None
        )
        return parse_heating_circuit(
            payload,
            previous_circuit,
            partial=partial,
        )
    raise ZontObjectParseError("Object type is not supported")


def _identity_int(payload: Mapping[str, Any], key: str, previous: int | None) -> int:
    """Read one required non-negative integer identity field."""
    value = payload.get(key, previous)
    if type(value) is not int or value < 0:
        raise ZontObjectParseError(f"Object {key} is invalid")
    return value


def _number_field(
    payload: Mapping[str, Any],
    key: str,
    previous: ZontDigitalBusAdapterData | None,
    partial: bool,
) -> float | None:
    """Read an optional numeric field, preserving it for partial updates."""
    previous_value = _previous_field(previous, key) if previous is not None else None
    return _optional_number(payload, key, previous_value, partial)


def _optional_number(
    payload: Mapping[str, Any],
    key: str,
    previous: float | None,
    partial: bool,
) -> float | None:
    """Read one finite optional number and preserve it for partial updates."""
    if key not in payload:
        return previous if partial else None
    value = payload[key]
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isfinite(value)
    ):
        return float(value)
    return None


def _object_available(
    payload: Mapping[str, Any],
    previous: bool | None,
    partial: bool,
    has_state: bool,
) -> bool:
    """Resolve documented object availability with a tolerant fallback."""
    if "a" not in payload:
        if partial and previous is not None:
            return previous
        return has_state
    available = payload["a"]
    return type(available) is int and available == 1


def _optional_binary_state(
    payload: Mapping[str, Any],
    key: str,
    previous: bool | None,
    partial: bool,
) -> bool | None:
    """Read an optional protocol flag encoded as exactly zero or one."""
    if key not in payload:
        return previous if partial else None
    value = payload[key]
    if type(value) is int and value in (0, 1):
        return value == 1
    return None


def _optional_non_negative_int(
    payload: Mapping[str, Any],
    key: str,
    previous: int | None,
    partial: bool,
) -> int | None:
    """Read optional non-negative configuration metadata."""
    if key not in payload:
        return previous if partial else None
    value = payload[key]
    return value if type(value) is int and value >= 0 else None


def _optional_heating_circuit_mode(
    payload: Mapping[str, Any],
    previous: ZontHeatingCircuitMode | None,
    partial: bool,
) -> ZontHeatingCircuitMode | None:
    """Read an optional documented heating circuit mode."""
    if "m" not in payload:
        return previous if partial else None
    value = payload["m"]
    try:
        return ZontHeatingCircuitMode(value)
    except (TypeError, ValueError):
        return None


def _integer_field(
    payload: Mapping[str, Any],
    key: str,
    previous: ZontDigitalBusAdapterData | None,
    partial: bool,
) -> int | None:
    """Read an optional integer field, preserving it for partial updates."""
    if key not in payload:
        if partial and previous is not None:
            return previous.error_code
        return None
    value = payload[key]
    return value if type(value) is int else None


def _state_field(
    payload: Mapping[str, Any],
    previous: ZontDigitalBusAdapterData | None,
    partial: bool,
) -> ZontDigitalBusState | None:
    """Read the documented numeric boiler state."""
    if "state" not in payload:
        if partial and previous is not None:
            return previous.state
        return None
    value = payload["state"]
    if type(value) is not int:
        return None
    return {
        0: ZontDigitalBusState.OFF,
        1: ZontDigitalBusState.RUNNING,
        2: ZontDigitalBusState.ERROR,
    }.get(value)


def _previous_field(
    previous: ZontDigitalBusAdapterData,
    key: str,
) -> float | None:
    """Return the model field corresponding to one wire key."""
    return {
        "water": previous.flow_temperature,
        "dhw": previous.dhw_temperature,
        "return": previous.return_temperature,
        "modul": previous.modulation,
        "press": previous.pressure,
    }[key]
