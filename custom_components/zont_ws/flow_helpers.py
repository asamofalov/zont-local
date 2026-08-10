"""Shared constants, selectors, and validation for ZONT flows."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_SCAN_INTERVAL, UnitOfTime
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_DHW_ON_TEMPERATURE,
    CONF_HEATING_OFF_MODE_ID,
    CONF_IMPORTED_OBJECT_IDS,
    DEFAULT_SCAN_INTERVAL,
    DHW_DEFAULT_ON_TEMPERATURE,
    DHW_MAX_TARGET_TEMPERATURE,
    DHW_MIN_TARGET_TEMPERATURE,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .heating_config import ZontHeatingModeConfiguration
from .object_import import ZontImportableObject

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_CANNOT_READ_DEVICES = "cannot_read_devices"
ERROR_CANNOT_IDENTIFY = "cannot_identify"
ERROR_CANNOT_READ_MODES = "cannot_read_modes"
ERROR_DIFFERENT_CONTROLLER = "different_controller"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_INVALID_DHW_ON_TEMPERATURE = "invalid_dhw_on_temperature"
ERROR_INVALID_DEVICE_SELECTION = "invalid_device_selection"
ERROR_INVALID_HOST = "invalid_host"
ERROR_INVALID_OFF_MODE = "invalid_off_mode"
ERROR_INVALID_SCAN_INTERVAL = "invalid_scan_interval"
ERROR_INVALID_EXPORT_SOURCE = "invalid_export_source"
ERROR_INVALID_EXPORT_TARGET = "invalid_export_target"
ERROR_DUPLICATE_EXPORT_SOURCE = "duplicate_export_source"
ERROR_DUPLICATE_EXPORT_TARGET = "duplicate_export_target"
ERROR_EXPORT_COMMAND_REJECTED = "export_command_rejected"
ERROR_EXPORT_NAME_IN_USE = "export_name_in_use"
ERROR_NO_OFF_MODE = "no_off_mode"
ERROR_UNKNOWN = "unknown"

FIELD_EXPORT_ENTITY_ID = "entity_id"
FIELD_EXPORT_NAME = "name"
FIELD_EXPORT_TARGET_ID = "target_id"
FIELD_EXPORT_BINDING_ID = "binding_id"


def _heating_mode_schema(
    modes: tuple[ZontHeatingModeConfiguration, ...],
    default: int | None = None,
    dhw_on_temperature: float = DHW_DEFAULT_ON_TEMPERATURE,
    scan_interval: int = DEFAULT_SCAN_INTERVAL,
) -> vol.Schema:
    """Return selectors for safe heating on and off behavior."""
    options = [
        SelectOptionDict(
            value=str(mode.object_id),
            label=f"{mode.name} (ID {mode.object_id})",
        )
        for mode in modes
    ]
    marker: vol.Marker = vol.Required(CONF_HEATING_OFF_MODE_ID)
    if default is not None and any(mode.object_id == default for mode in modes):
        marker = vol.Required(CONF_HEATING_OFF_MODE_ID, default=str(default))
    return vol.Schema(
        {
            marker: SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    custom_value=False,
                )
            ),
            vol.Required(
                CONF_DHW_ON_TEMPERATURE,
                default=dhw_on_temperature,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=DHW_MIN_TARGET_TEMPERATURE,
                    max=DHW_MAX_TARGET_TEMPERATURE,
                    step=1.0,
                    unit_of_measurement="°C",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=scan_interval,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=1,
                    unit_of_measurement=UnitOfTime.SECONDS,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _devices_schema(
    descriptors: tuple[ZontImportableObject, ...],
    selected_ids: frozenset[int],
    *,
    auto_import_new: bool,
) -> vol.Schema:
    """Return selectors for child-device exposure settings."""
    options = [
        SelectOptionDict(
            value=str(descriptor.object_id),
            label=descriptor.selector_label,
        )
        for descriptor in descriptors
    ]
    return vol.Schema(
        {
            vol.Required(
                CONF_IMPORTED_OBJECT_IDS,
                default=[
                    str(descriptor.object_id)
                    for descriptor in descriptors
                    if descriptor.object_id in selected_ids
                ],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    custom_value=False,
                )
            ),
            vol.Required(
                CONF_AUTO_IMPORT_NEW_OBJECTS,
                default=auto_import_new,
            ): BooleanSelector(),
        }
    )


def _validate_selected_object_ids(
    user_input: dict[str, Any],
    valid_ids: frozenset[int],
) -> frozenset[int] | None:
    """Return a validated object selection from a multiple select field."""
    values = user_input.get(CONF_IMPORTED_OBJECT_IDS)
    if not isinstance(values, list | tuple):
        return None
    selected_ids: set[int] = set()
    try:
        for value in values:
            if isinstance(value, bool):
                return None
            object_id = int(value)
            if str(object_id) != str(value) or object_id not in valid_ids:
                return None
            selected_ids.add(object_id)
    except (TypeError, ValueError):
        return None
    return frozenset(selected_ids)


def _validate_dhw_on_temperature(user_input: dict[str, Any]) -> float | None:
    """Return a valid configured DHW on temperature."""
    value = user_input.get(CONF_DHW_ON_TEMPERATURE, DHW_DEFAULT_ON_TEMPERATURE)
    return float(value) if _is_valid_dhw_on_temperature(value) else None


def _is_valid_dhw_on_temperature(value: Any) -> bool:
    """Return whether a value is a supported DHW on temperature."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and DHW_MIN_TARGET_TEMPERATURE <= value <= DHW_MAX_TARGET_TEMPERATURE
    )


def _validate_scan_interval(user_input: dict[str, Any]) -> int | None:
    """Return a valid control-poll interval in seconds."""
    value = user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not float(value).is_integer()
        or not MIN_SCAN_INTERVAL <= value <= MAX_SCAN_INTERVAL
    ):
        return None
    return int(value)


def _valid_scan_interval_or_default(value: Any) -> int:
    """Return a stored interval or the backward-compatible default."""
    validated = _validate_scan_interval({CONF_SCAN_INTERVAL: value})
    return validated if validated is not None else DEFAULT_SCAN_INTERVAL
