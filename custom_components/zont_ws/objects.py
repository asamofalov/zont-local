"""Typed ZONT object models and protocol parsers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any

OBJECT_TYPE_DIGITAL_BUS_ADAPTER = 6


class ZontObjectParseError(ValueError):
    """Raised when a ZONT object payload has no usable identity."""


class ZontDigitalBusState(StrEnum):
    """Documented operating states of a digital bus adapter."""

    OFF = "off"
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ZontObjectData:
    """Common descriptive data for one ZONT object."""

    object_id: int
    object_type: int
    name: str
    available: bool = True


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


type ZontObject = ZontDigitalBusAdapterData


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
    if key not in payload:
        if partial and previous is not None:
            return _previous_field(previous, key)
        return None
    value = payload[key]
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isfinite(value)
    ):
        return float(value)
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
