"""Configuration models and wire helpers for Home Assistant exports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..const import (
    CONF_EXPORT_KIND,
    CONF_EXPORT_SOURCE,
    CONF_EXPORT_TARGET_ID,
    CONF_EXPORT_TARGET_NAME,
    CONF_EXPORT_TARGET_SUBTYPE,
    CONF_EXPORTS,
    EXPORT_BINARY_TIMEOUT,
)
from ..protocol.objects import (
    ANALOG_INPUT_SUBTYPE_DISCRETE_NC,
    ANALOG_INPUT_SUBTYPE_DISCRETE_NO,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
)

type ZontExportValue = bool | float

BINARY_EXPORT_SUBTYPES = frozenset(
    {
        ANALOG_INPUT_SUBTYPE_DISCRETE_NO,
        ANALOG_INPUT_SUBTYPE_DISCRETE_NC,
    }
)


class ZontExportKind(StrEnum):
    """Supported Home Assistant values exported to ZONT."""

    TEMPERATURE = "temperature"
    BINARY = "binary"


@dataclass(frozen=True, slots=True)
class ZontExportBinding:
    """One Home Assistant source bound to one ZONT object."""

    kind: ZontExportKind
    source: str
    target_id: int
    target_name: str
    target_subtype: int | None = None

    def as_dict(self) -> dict[str, str | int]:
        """Return the config-entry representation of the binding."""
        stored: dict[str, str | int] = {
            CONF_EXPORT_KIND: self.kind,
            CONF_EXPORT_SOURCE: self.source,
            CONF_EXPORT_TARGET_ID: self.target_id,
            CONF_EXPORT_TARGET_NAME: self.target_name,
        }
        if self.target_subtype is not None:
            stored[CONF_EXPORT_TARGET_SUBTYPE] = self.target_subtype
        return stored


def export_bindings(options: Mapping[str, Any]) -> tuple[ZontExportBinding, ...]:
    """Return valid export bindings with globally unique sources and targets."""
    stored = options.get(CONF_EXPORTS, [])
    if not isinstance(stored, list | tuple):
        return ()

    bindings: list[ZontExportBinding] = []
    sources: set[str] = set()
    targets: set[int] = set()
    for item in stored:
        binding = _binding_from_mapping(item)
        if binding is None or binding.source in sources or binding.target_id in targets:
            continue
        sources.add(binding.source)
        targets.add(binding.target_id)
        bindings.append(binding)
    return tuple(bindings)


def export_target_ids(options: Mapping[str, Any]) -> frozenset[int]:
    """Return object IDs reserved as export targets."""
    return frozenset(binding.target_id for binding in export_bindings(options))


def command_response_id(response: Mapping[str, Any]) -> int | None:
    """Return one unambiguous object ID from a command response."""
    lower = response.get("id")
    upper = response.get("Id")
    if lower is not None and upper is not None and lower != upper:
        return None
    value = lower if lower is not None else upper
    return value if type(value) is int and value >= 0 else None


def export_command(kind: ZontExportKind, value: ZontExportValue) -> str:
    """Return the confirmed ZONT command for one typed export value."""
    if kind is ZontExportKind.TEMPERATURE and type(value) is float:
        return export_temperature_command(value)
    if kind is ZontExportKind.BINARY and type(value) is bool:
        return export_binary_command(value)
    raise ValueError("Export value does not match its kind")


def export_temperature_command(value: float) -> str:
    """Return the confirmed ZONT command for a Celsius temperature."""
    return f"1 {value:.1f}"


def export_binary_command(is_on: bool) -> str:
    """Return the confirmed virtual discrete-input command."""
    value = 20 if is_on else 0
    return f"0 {value} {EXPORT_BINARY_TIMEOUT}"


def export_target_protocol_identity(
    kind: ZontExportKind,
    target_subtype: int | None = None,
) -> tuple[int, int | None]:
    """Return the protocol type and optional subtype for an export kind."""
    if kind is ZontExportKind.TEMPERATURE:
        return OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR, None
    if target_subtype not in BINARY_EXPORT_SUBTYPES:
        raise ValueError("Binary export subtype must be 19 or 20")
    return OBJECT_TYPE_ANALOG_INPUT, target_subtype


def export_target_matches(
    kind: ZontExportKind,
    response: Mapping[str, Any],
    target_subtype: int | None = None,
) -> bool:
    """Return whether a state response identifies a compatible target."""
    if response.get("failed"):
        return False
    try:
        object_type, object_subtype = export_target_protocol_identity(
            kind,
            target_subtype,
        )
    except ValueError:
        return False
    response_type = response.get("type")
    if type(response_type) is not int or response_type != object_type:
        return False
    if object_subtype is None:
        return True
    response_subtype = response.get("stype")
    return type(response_subtype) is int and response_subtype == object_subtype


def _binding_from_mapping(
    item: Any,
) -> ZontExportBinding | None:
    """Parse one strict stored binding."""
    if not isinstance(item, Mapping):
        return None
    raw_kind = item.get(CONF_EXPORT_KIND)
    try:
        kind = ZontExportKind(raw_kind)
    except (TypeError, ValueError):
        return None
    source = item.get(CONF_EXPORT_SOURCE)
    target_id = item.get(CONF_EXPORT_TARGET_ID)
    target_name = item.get(CONF_EXPORT_TARGET_NAME)
    target_subtype = item.get(CONF_EXPORT_TARGET_SUBTYPE)
    if (
        not isinstance(source, str)
        or not source.strip()
        or type(target_id) is not int
        or target_id < 0
        or not isinstance(target_name, str)
        or not target_name.strip()
    ):
        return None
    if kind is ZontExportKind.BINARY:
        if (
            type(target_subtype) is not int
            or target_subtype not in BINARY_EXPORT_SUBTYPES
        ):
            return None
    elif target_subtype is not None:
        return None
    return ZontExportBinding(
        kind,
        source.strip(),
        target_id,
        target_name.strip(),
        target_subtype,
    )
