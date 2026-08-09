"""Internal ZONT heating configuration parsers and setpoint metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any

from .objects import OBJECT_TYPE_HEATING_CIRCUIT

OBJECT_TYPE_WIRED_TEMPERATURE_SENSOR = 1
OBJECT_TYPE_RADIO_SENSOR = 8
OBJECT_TYPE_ANALOG_TEMPERATURE_SENSOR = 27
SUPPORTED_TEMPERATURE_SENSOR_CONFIG_TYPES = frozenset(
    {
        OBJECT_TYPE_WIRED_TEMPERATURE_SENSOR,
        OBJECT_TYPE_RADIO_SENSOR,
        OBJECT_TYPE_ANALOG_TEMPERATURE_SENSOR,
    }
)

CONSUMER_CIRCUIT_SUBTYPE = 3
WEATHER_COMPENSATION_REQUEST_ONLY_FLAG = 128
SLAVE_MODE_FLAG = 1_048_576

AIR_MIN_TEMPERATURE = 5.0
AIR_MAX_TEMPERATURE = 40.0
WEATHER_COMPENSATION_MIN_TEMPERATURE = 5.0
WEATHER_COMPENSATION_MAX_TEMPERATURE = 35.0
WATER_MIN_TEMPERATURE = -30.0
WATER_MAX_TEMPERATURE = 100.0

_MAX_RESPONSE_LENGTH = 65_536
_MAX_NESTING_DEPTH = 8


class ZontHeatingConfigParseError(ValueError):
    """Raised when an internal ZONT heating response is malformed."""


class ZontConsumerControlMode(StrEnum):
    """Base consumer-circuit control modes."""

    AIR = "air"
    AIR_PID = "air_pid"
    WATER = "water"


@dataclass(frozen=True, slots=True)
class ZontHeatingCircuitConfiguration:
    """Configuration fields needed to determine a circuit setpoint range."""

    object_id: int
    name: str
    subtype: int
    water_min_temperature: float | None
    water_max_temperature: float | None
    air_temperature_sensor_id: int | None
    air_temperature_reserve_sensor_id: int | None
    water_temperature_sensor_id: int | None
    water_temperature_reserve_sensor_id: int | None
    setting_register: int
    external_thermostat_id: int | None
    pza: int | None
    heat_source_id: int | None

    @property
    def has_weather_compensation(self) -> bool:
        """Return whether the circuit has weather-dependent control configured."""
        return self.pza not in (None, 0, 255)

    @property
    def uses_weather_compensated_setpoint(self) -> bool:
        """Return whether weather compensation changes the setpoint semantics."""
        return (
            self.setting_register & 3 == 2
            and self.has_weather_compensation
            and not self.setting_register & WEATHER_COMPENSATION_REQUEST_ONLY_FLAG
        )

    @property
    def is_slave(self) -> bool:
        """Return whether this circuit follows a master circuit."""
        return bool(self.setting_register & SLAVE_MODE_FLAG)


@dataclass(frozen=True, slots=True)
class ZontHeatingCircuitInternalState:
    """Internal state fields needed for setpoint metadata."""

    object_id: int
    target_sensor_id: int | None


@dataclass(frozen=True, slots=True)
class ZontTemperatureSensorConfiguration:
    """Temperature limits configured for a ZONT sensor."""

    object_id: int
    object_type: int
    lower_threshold: float | None
    upper_threshold: float | None


@dataclass(frozen=True, slots=True)
class ZontHeatingCircuitControlData:
    """Resolved Home Assistant control capabilities of a consumer circuit."""

    control_mode: ZontConsumerControlMode | None
    has_weather_compensation: bool
    target_sensor_id: int | None
    min_temperature: float | None
    max_temperature: float | None

    @property
    def can_set_temperature(self) -> bool:
        """Return whether a safe, valid setpoint range is available."""
        return self.min_temperature is not None and self.max_temperature is not None


def immutable_heating_controls(
    controls: Mapping[int, ZontHeatingCircuitControlData] | None = None,
) -> Mapping[int, ZontHeatingCircuitControlData]:
    """Return an immutable copy of resolved circuit controls."""
    return MappingProxyType(dict(controls or {}))


def parse_heating_circuit_configuration(
    response: str,
    expected_object_id: int | None = None,
) -> ZontHeatingCircuitConfiguration:
    """Parse the supported fields of a ``#Z<id>`` heating-circuit response."""
    object_id, fields = _parse_response(response, "#Z", ":")
    _validate_expected_id(object_id, expected_object_id)
    if len(fields) < 17 or _required_int(fields, 0) != OBJECT_TYPE_HEATING_CIRCUIT:
        raise ZontHeatingConfigParseError(
            "Response is not a supported heating-circuit configuration"
        )

    name = fields[1]
    if not isinstance(name, str) or not name.strip():
        raise ZontHeatingConfigParseError("Heating-circuit name is missing")

    return ZontHeatingCircuitConfiguration(
        object_id=object_id,
        name=name.strip(),
        subtype=_non_negative_int(fields, 2),
        water_min_temperature=_configuration_temperature(fields, 3),
        water_max_temperature=_configuration_temperature(fields, 4),
        air_temperature_sensor_id=_optional_object_id(fields, 5),
        air_temperature_reserve_sensor_id=_optional_object_id(fields, 6),
        water_temperature_sensor_id=_optional_object_id(fields, 7),
        water_temperature_reserve_sensor_id=_optional_object_id(fields, 8),
        setting_register=_non_negative_int(fields, 11),
        external_thermostat_id=_optional_object_id(fields, 13),
        pza=_optional_non_negative_int(fields, 15),
        heat_source_id=_optional_object_id(fields, 16),
    )


def parse_heating_circuit_internal_state(
    response: str,
    expected_object_id: int | None = None,
) -> ZontHeatingCircuitInternalState:
    """Parse the supported fields of a ``#Y<id>`` heating-circuit response."""
    object_id, fields = _parse_response(response, "#Y", "$")
    _validate_expected_id(object_id, expected_object_id)
    if len(fields) < 7:
        raise ZontHeatingConfigParseError(
            "Heating-circuit state does not contain a target sensor"
        )
    return ZontHeatingCircuitInternalState(
        object_id=object_id,
        target_sensor_id=_optional_object_id(fields, 6),
    )


def parse_temperature_sensor_configuration(
    response: str,
    expected_object_id: int | None = None,
) -> ZontTemperatureSensorConfiguration:
    """Parse temperature thresholds from a supported ``#Z<id>`` response."""
    object_id, fields = _parse_response(response, "#Z", ":")
    _validate_expected_id(object_id, expected_object_id)
    object_type = _required_int(fields, 0)
    if object_type not in SUPPORTED_TEMPERATURE_SENSOR_CONFIG_TYPES:
        raise ZontHeatingConfigParseError(
            "Response is not a supported temperature-sensor configuration"
        )

    threshold_indexes = {
        OBJECT_TYPE_WIRED_TEMPERATURE_SENSOR: (4, 3),
        OBJECT_TYPE_RADIO_SENSOR: (5, 4),
        OBJECT_TYPE_ANALOG_TEMPERATURE_SENSOR: (5, 4),
    }
    lower_index, upper_index = threshold_indexes[object_type]
    if len(fields) <= max(lower_index, upper_index):
        raise ZontHeatingConfigParseError(
            "Temperature-sensor configuration is incomplete"
        )
    return ZontTemperatureSensorConfiguration(
        object_id=object_id,
        object_type=object_type,
        lower_threshold=_configuration_temperature(fields, lower_index),
        upper_threshold=_configuration_temperature(fields, upper_index),
    )


def resolve_heating_circuit_control(
    configuration: ZontHeatingCircuitConfiguration,
    target_sensor_id: int | None,
    sensor_configuration: ZontTemperatureSensorConfiguration | None = None,
) -> ZontHeatingCircuitControlData:
    """Resolve a safe setpoint range using the official web-client rules."""
    mode = _base_control_mode(configuration)
    unsupported = (
        configuration.subtype != CONSUMER_CIRCUIT_SUBTYPE
        or mode is None
        or configuration.is_slave
        or (
            configuration.external_thermostat_id is not None
            and not configuration.has_weather_compensation
        )
    )
    if unsupported:
        return ZontHeatingCircuitControlData(
            control_mode=mode,
            has_weather_compensation=configuration.has_weather_compensation,
            target_sensor_id=target_sensor_id,
            min_temperature=None,
            max_temperature=None,
        )

    if mode in (ZontConsumerControlMode.AIR, ZontConsumerControlMode.AIR_PID):
        min_temperature = (
            sensor_configuration.lower_threshold
            if sensor_configuration is not None
            and sensor_configuration.lower_threshold is not None
            else AIR_MIN_TEMPERATURE
        )
        max_temperature = (
            sensor_configuration.upper_threshold
            if sensor_configuration is not None
            and sensor_configuration.upper_threshold is not None
            else AIR_MAX_TEMPERATURE
        )
    elif configuration.uses_weather_compensated_setpoint:
        min_temperature = WEATHER_COMPENSATION_MIN_TEMPERATURE
        max_temperature = WEATHER_COMPENSATION_MAX_TEMPERATURE
    else:
        min_temperature = configuration.water_min_temperature
        max_temperature = configuration.water_max_temperature
        if min_temperature is None and sensor_configuration is not None:
            min_temperature = sensor_configuration.lower_threshold
        if max_temperature is None and sensor_configuration is not None:
            max_temperature = sensor_configuration.upper_threshold
        min_temperature = (
            WATER_MIN_TEMPERATURE if min_temperature is None else min_temperature
        )
        max_temperature = (
            WATER_MAX_TEMPERATURE if max_temperature is None else max_temperature
        )

    if (
        not isfinite(min_temperature)
        or not isfinite(max_temperature)
        or min_temperature >= max_temperature
    ):
        return ZontHeatingCircuitControlData(
            control_mode=mode,
            has_weather_compensation=configuration.has_weather_compensation,
            target_sensor_id=target_sensor_id,
            min_temperature=None,
            max_temperature=None,
        )
    return ZontHeatingCircuitControlData(
        control_mode=mode,
        has_weather_compensation=configuration.has_weather_compensation,
        target_sensor_id=target_sensor_id,
        min_temperature=min_temperature,
        max_temperature=max_temperature,
    )


def _base_control_mode(
    configuration: ZontHeatingCircuitConfiguration,
) -> ZontConsumerControlMode | None:
    """Return the base control mode independently of weather compensation."""
    try:
        return {
            0: ZontConsumerControlMode.AIR,
            1: ZontConsumerControlMode.AIR_PID,
            2: ZontConsumerControlMode.WATER,
        }[configuration.setting_register & 3]
    except KeyError:
        return None


def _parse_response(
    response: str,
    prefix: str,
    delimiter: str,
) -> tuple[int, tuple[Any, ...]]:
    """Parse the identity and positional values of one internal response."""
    if not isinstance(response, str) or len(response) > _MAX_RESPONSE_LENGTH:
        raise ZontHeatingConfigParseError("Internal response is not valid text")
    response = response.strip()
    separator = response.find(delimiter, len(prefix))
    if not response.startswith(prefix) or separator < 0:
        raise ZontHeatingConfigParseError("Internal response has an invalid prefix")
    object_id_text = response[len(prefix) : separator]
    try:
        object_id = int(object_id_text, 10)
    except ValueError as err:
        raise ZontHeatingConfigParseError(
            "Internal response has an invalid ID"
        ) from err
    if object_id < 0 or str(object_id) != object_id_text:
        raise ZontHeatingConfigParseError("Internal response has an invalid ID")
    return object_id, _PositionalParser(response[separator + 1 :]).parse()


class _PositionalParser:
    """Parse comma-separated ZONT values without evaluating controller text."""

    def __init__(self, value: str) -> None:
        self._value = value
        self._position = 0

    def parse(self) -> tuple[Any, ...]:
        """Parse all top-level values."""
        self._skip_whitespace()
        if self._at_end():
            raise ZontHeatingConfigParseError("Internal response has no fields")
        values = self._parse_sequence(None, 0)
        self._skip_whitespace()
        if not self._at_end():
            raise ZontHeatingConfigParseError("Internal response has trailing data")
        return tuple(values)

    def _parse_sequence(self, closing: str | None, depth: int) -> list[Any]:
        values: list[Any] = []
        self._skip_whitespace()
        if closing is not None and self._peek() == closing:
            self._position += 1
            return values
        while True:
            values.append(self._parse_value(depth))
            self._skip_whitespace()
            if closing is not None and self._peek() == closing:
                self._position += 1
                return values
            if self._at_end():
                if closing is not None:
                    raise ZontHeatingConfigParseError("Internal array is not closed")
                return values
            if self._peek() != ",":
                raise ZontHeatingConfigParseError("Internal fields are not separated")
            self._position += 1
            self._skip_whitespace()
            if self._at_end() or (closing is not None and self._peek() == closing):
                raise ZontHeatingConfigParseError(
                    "Internal response has an empty field"
                )

    def _parse_value(self, depth: int) -> Any:
        if depth > _MAX_NESTING_DEPTH:
            raise ZontHeatingConfigParseError("Internal response is nested too deeply")
        current = self._peek()
        if current == "[":
            self._position += 1
            return self._parse_sequence("]", depth + 1)
        if current in ("'", '"'):
            return self._parse_string(current)
        return self._parse_atom()

    def _parse_string(self, quote: str) -> str:
        self._position += 1
        result: list[str] = []
        while not self._at_end():
            current = self._peek()
            self._position += 1
            if current == quote:
                if self._peek() == quote:
                    result.append(quote)
                    self._position += 1
                    continue
                return "".join(result)
            if current == "\\":
                if self._at_end():
                    break
                current = self._peek()
                self._position += 1
            result.append(current)
        raise ZontHeatingConfigParseError("Internal string is not closed")

    def _parse_atom(self) -> Any:
        start = self._position
        while not self._at_end() and self._peek() not in ",]":
            self._position += 1
        token = self._value[start : self._position].strip()
        if not token:
            raise ZontHeatingConfigParseError("Internal response has an empty field")
        if token in ("null", "None"):
            return None
        try:
            return int(token, 10)
        except ValueError:
            try:
                number = float(token)
            except ValueError:
                return token
            return number if isfinite(number) else token

    def _skip_whitespace(self) -> None:
        while not self._at_end() and self._peek().isspace():
            self._position += 1

    def _peek(self) -> str:
        if self._at_end():
            return ""
        return self._value[self._position]

    def _at_end(self) -> bool:
        return self._position >= len(self._value)


def _validate_expected_id(object_id: int, expected_object_id: int | None) -> None:
    if expected_object_id is not None and object_id != expected_object_id:
        raise ZontHeatingConfigParseError("Internal response ID does not match request")


def _required_int(fields: tuple[Any, ...], index: int) -> int:
    if index >= len(fields) or type(fields[index]) is not int:
        raise ZontHeatingConfigParseError("Required internal integer is missing")
    return fields[index]


def _non_negative_int(fields: tuple[Any, ...], index: int) -> int:
    value = _required_int(fields, index)
    if value < 0:
        raise ZontHeatingConfigParseError("Internal integer must not be negative")
    return value


def _optional_non_negative_int(fields: tuple[Any, ...], index: int) -> int | None:
    if index >= len(fields):
        return None
    value = fields[index]
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ZontHeatingConfigParseError("Optional internal integer is invalid")
    return value


def _optional_object_id(fields: tuple[Any, ...], index: int) -> int | None:
    value = _optional_non_negative_int(fields, index)
    return None if value in (None, 0, 65_535) else value


def _configuration_temperature(fields: tuple[Any, ...], index: int) -> float | None:
    value = _optional_non_negative_int(fields, index)
    if value in (None, 0, 65_535):
        return None
    return value / 10 - 273
