"""Options flow for the ZONT WebSocket integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
)

from ..const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_DHW_ON_TEMPERATURE,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_HEATING_OFF_MODE_ID,
    CONF_IMPORTED_OBJECT_IDS,
    DHW_DEFAULT_ON_TEMPERATURE,
)
from ..object_import import (
    ZontImportableObject,
    importable_object_descriptors,
    object_import_configuration,
)
from ..protocol.heating_config import ZontHeatingModeConfiguration
from ..protocol.objects import (
    ZontObject,
)
from .discovery import _async_get_entry_objects, _async_get_entry_off_modes
from .export import _TemperatureExportOptionsFlowSteps
from .schemas import (
    ERROR_INVALID_DEVICE_SELECTION,
    ERROR_INVALID_DHW_ON_TEMPERATURE,
    ERROR_INVALID_OFF_MODE,
    ERROR_INVALID_SCAN_INTERVAL,
    ERROR_NO_OFF_MODE,
    _devices_schema,
    _heating_mode_schema,
    _is_valid_dhw_on_temperature,
    _valid_scan_interval_or_default,
    _validate_dhw_on_temperature,
    _validate_scan_interval,
    _validate_selected_object_ids,
)

_LOGGER = logging.getLogger(__name__)


class ZontWsOptionsFlow(
    _TemperatureExportOptionsFlowSteps,
    config_entries.OptionsFlow,
):
    """Manage changeable ZONT integration behavior."""

    def __init__(self) -> None:
        """Initialize options discovered for this flow instance."""
        self._off_modes: tuple[ZontHeatingModeConfiguration, ...] = ()
        self._off_modes_error: str | None = None
        self._off_modes_loaded = False
        self._objects: dict[int, ZontObject] = {}
        self._objects_error: str | None = None
        self._objects_loaded = False
        self._export_source_entity_id: str | None = None
        self._export_target_id: int | None = None
        self._managed_export_target_id: int | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show sections of changeable integration settings."""
        return self.async_show_menu(
            step_id="init",
            menu_options=("general", "devices", "exports"),
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change controller-wide heating and polling settings."""
        if not self._off_modes_loaded:
            self._off_modes, self._off_modes_error = await _async_get_entry_off_modes(
                self.hass, self.config_entry
            )
            self._off_modes_loaded = True
        off_modes = self._off_modes
        error = self._off_modes_error
        if error is not None or not off_modes:
            return self.async_show_form(
                step_id="general",
                data_schema=vol.Schema({}),
                errors={"base": error or ERROR_NO_OFF_MODE},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mode_id = int(user_input[CONF_HEATING_OFF_MODE_ID])
            except (KeyError, TypeError, ValueError):
                errors["base"] = ERROR_INVALID_OFF_MODE
            else:
                temperature = _validate_dhw_on_temperature(user_input)
                if temperature is None:
                    errors["base"] = ERROR_INVALID_DHW_ON_TEMPERATURE
                elif (scan_interval := _validate_scan_interval(user_input)) is None:
                    errors["base"] = ERROR_INVALID_SCAN_INTERVAL
                elif mode_id in {mode.object_id for mode in off_modes}:
                    return self.async_create_entry(
                        data={
                            **self.config_entry.options,
                            CONF_HEATING_OFF_MODE_ID: mode_id,
                            CONF_DHW_ON_TEMPERATURE: temperature,
                            CONF_SCAN_INTERVAL: scan_interval,
                        }
                    )
                else:
                    errors["base"] = ERROR_INVALID_OFF_MODE

        current_mode_id = self.config_entry.options.get(CONF_HEATING_OFF_MODE_ID)
        current_dhw_temperature = self.config_entry.options.get(
            CONF_DHW_ON_TEMPERATURE,
            DHW_DEFAULT_ON_TEMPERATURE,
        )
        current_scan_interval = _valid_scan_interval_or_default(
            self.config_entry.options.get(CONF_SCAN_INTERVAL)
        )
        return self.async_show_form(
            step_id="general",
            data_schema=_heating_mode_schema(
                off_modes,
                current_mode_id if type(current_mode_id) is int else None,
                (
                    float(current_dhw_temperature)
                    if _is_valid_dhw_on_temperature(current_dhw_temperature)
                    else DHW_DEFAULT_ON_TEMPERATURE
                ),
                current_scan_interval,
            ),
            errors=errors,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change which ZONT objects are exposed in Home Assistant."""
        if not self._objects_loaded:
            self._objects, self._objects_error = await _async_get_entry_objects(
                self.hass, self.config_entry
            )
            self._objects_loaded = True
        if self._objects_error is not None:
            return self.async_show_form(
                step_id="devices",
                data_schema=vol.Schema({}),
                errors={"base": self._objects_error},
            )

        configuration = object_import_configuration(self.config_entry.options)
        descriptors = [
            descriptor
            for descriptor in importable_object_descriptors(self._objects)
            if descriptor.object_id not in configuration.exported_ids
        ]
        discovered_ids = {descriptor.object_id for descriptor in descriptors}
        for object_id in sorted(
            configuration.imported_ids - discovered_ids - configuration.exported_ids
        ):
            descriptors.append(
                ZontImportableObject(
                    object_id=object_id,
                    name="Название неизвестно",
                    device_type="Недоступное устройство",
                    manufacturer=None,
                    model="Недоступное устройство",
                )
            )
        descriptors.sort(key=lambda descriptor: descriptor.sort_key)
        valid_ids = frozenset(descriptor.object_id for descriptor in descriptors)
        selected_ids = frozenset(
            object_id for object_id in valid_ids if configuration.imports(object_id)
        )

        errors: dict[str, str] = {}
        if user_input is not None:
            submitted_ids = _validate_selected_object_ids(user_input, valid_ids)
            auto_import = user_input.get(CONF_AUTO_IMPORT_NEW_OBJECTS)
            if submitted_ids is None or type(auto_import) is not bool:
                errors["base"] = ERROR_INVALID_DEVICE_SELECTION
            else:
                previous_excluded = configuration.excluded_ids
                return self.async_create_entry(
                    data={
                        **self.config_entry.options,
                        CONF_IMPORTED_OBJECT_IDS: sorted(submitted_ids),
                        CONF_EXCLUDED_OBJECT_IDS: sorted(
                            (previous_excluded | valid_ids) - submitted_ids
                        ),
                        CONF_AUTO_IMPORT_NEW_OBJECTS: auto_import,
                    }
                )

        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(
                tuple(descriptors),
                selected_ids,
                auto_import_new=configuration.auto_import_new,
            ),
            errors=errors,
        )
