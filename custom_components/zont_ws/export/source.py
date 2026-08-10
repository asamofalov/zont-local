"""Resolve and validate Home Assistant temperature export sources."""

from __future__ import annotations

from math import isfinite

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import TemperatureConverter

from ..const import DOMAIN


class ZontExportSourceError(ValueError):
    """Raised when a configured Home Assistant source is invalid."""


class ZontExportSourceUnavailable(ZontExportSourceError):
    """Raised when a valid source has no value to export right now."""


@callback
def export_source_reference(hass: HomeAssistant, entity_id: str) -> str:
    """Return a rename-safe reference for an entity when possible."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    return registry_entry.id if registry_entry is not None else entity_id


@callback
def resolve_export_source(hass: HomeAssistant, source: str) -> str | None:
    """Resolve a stored entity registry UUID or entity ID."""
    return er.async_resolve_entity_id(er.async_get(hass), source)


def export_temperature_from_state(state: State) -> float:
    """Validate and convert one Home Assistant temperature state to Celsius."""
    if state.domain != "sensor" or state.attributes.get(ATTR_DEVICE_CLASS) != (
        SensorDeviceClass.TEMPERATURE
    ):
        raise ZontExportSourceError("Source is not a temperature sensor")
    if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        raise ZontExportSourceUnavailable("Source has no current temperature")

    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if not isinstance(unit, str) or unit not in TemperatureConverter.VALID_UNITS:
        raise ZontExportSourceError("Source has an unsupported temperature unit")
    try:
        value = float(state.state)
        value = TemperatureConverter.convert(
            value,
            unit,
            UnitOfTemperature.CELSIUS,
        )
    except (TypeError, ValueError, HomeAssistantError) as err:
        raise ZontExportSourceError("Source temperature cannot be converted") from err
    if not isfinite(value):
        raise ZontExportSourceError("Source temperature is not finite")
    rounded = round(value, 1)
    return 0.0 if rounded == 0 else rounded


@callback
def validate_export_source(
    hass: HomeAssistant,
    entity_id: str,
    config_entry_id: str,
) -> float:
    """Validate a selectable source and return its Celsius temperature."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is not None and (
        registry_entry.platform == DOMAIN
        or registry_entry.config_entry_id == config_entry_id
    ):
        raise ZontExportSourceError("ZONT entities cannot be export sources")
    state = hass.states.get(entity_id)
    if state is None:
        raise ZontExportSourceError("Source entity does not exist")
    return export_temperature_from_state(state)
