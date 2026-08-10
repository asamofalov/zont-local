"""Configuration models and wire helpers for temperature exports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..const import (
    CONF_EXPORT_SOURCE,
    CONF_EXPORT_TARGET_ID,
    CONF_EXPORT_TARGET_NAME,
    CONF_TEMPERATURE_EXPORTS,
)


@dataclass(frozen=True, slots=True)
class ZontTemperatureExportBinding:
    """One Home Assistant source bound to one ZONT object."""

    source: str
    target_id: int
    target_name: str

    def as_dict(self) -> dict[str, str | int]:
        """Return the config-entry representation of the binding."""
        return {
            CONF_EXPORT_SOURCE: self.source,
            CONF_EXPORT_TARGET_ID: self.target_id,
            CONF_EXPORT_TARGET_NAME: self.target_name,
        }


def temperature_export_bindings(
    options: Mapping[str, Any],
) -> tuple[ZontTemperatureExportBinding, ...]:
    """Return valid, unambiguous temperature export bindings."""
    stored = options.get(CONF_TEMPERATURE_EXPORTS, [])
    if not isinstance(stored, list | tuple):
        return ()

    bindings: list[ZontTemperatureExportBinding] = []
    sources: set[str] = set()
    targets: set[int] = set()
    for item in stored:
        if not isinstance(item, Mapping):
            continue
        source = item.get(CONF_EXPORT_SOURCE)
        target_id = item.get(CONF_EXPORT_TARGET_ID)
        target_name = item.get(CONF_EXPORT_TARGET_NAME)
        if (
            not isinstance(source, str)
            or not source.strip()
            or type(target_id) is not int
            or target_id < 0
            or not isinstance(target_name, str)
            or not target_name.strip()
        ):
            continue
        source = source.strip()
        target_name = target_name.strip()
        if source in sources or target_id in targets:
            continue
        sources.add(source)
        targets.add(target_id)
        bindings.append(ZontTemperatureExportBinding(source, target_id, target_name))
    return tuple(bindings)


def temperature_export_target_ids(options: Mapping[str, Any]) -> frozenset[int]:
    """Return object IDs reserved as export targets."""
    return frozenset(
        binding.target_id for binding in temperature_export_bindings(options)
    )


def command_response_id(response: Mapping[str, Any]) -> int | None:
    """Return one unambiguous object ID from a command response."""
    lower = response.get("id")
    upper = response.get("Id")
    if lower is not None and upper is not None and lower != upper:
        return None
    value = lower if lower is not None else upper
    return value if type(value) is int and value >= 0 else None


def export_temperature_command(value: float) -> str:
    """Return the confirmed ZONT command for a Celsius temperature."""
    return f"1 {value:.1f}"
