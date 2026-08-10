"""Options flow for the ZONT WebSocket integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    ZontRequestTimeoutError,
    async_open_temporary_request_session,
)
from .const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_DHW_ON_TEMPERATURE,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_HEATING_OFF_MODE_ID,
    CONF_IMPORTED_OBJECT_IDS,
    CONF_TEMPERATURE_EXPORTS,
    DHW_DEFAULT_ON_TEMPERATURE,
    DOMAIN,
)
from .controller import (
    controller_websocket_url,
)
from .coordinator import ZontData
from .flow_helpers import (
    ERROR_CANNOT_CONNECT,
    ERROR_CANNOT_READ_DEVICES,
    ERROR_CANNOT_READ_MODES,
    ERROR_DUPLICATE_EXPORT_SOURCE,
    ERROR_DUPLICATE_EXPORT_TARGET,
    ERROR_EXPORT_COMMAND_REJECTED,
    ERROR_EXPORT_NAME_IN_USE,
    ERROR_INVALID_AUTH,
    ERROR_INVALID_DEVICE_SELECTION,
    ERROR_INVALID_DHW_ON_TEMPERATURE,
    ERROR_INVALID_EXPORT_SOURCE,
    ERROR_INVALID_EXPORT_TARGET,
    ERROR_INVALID_OFF_MODE,
    ERROR_INVALID_SCAN_INTERVAL,
    ERROR_NO_OFF_MODE,
    ERROR_UNKNOWN,
    FIELD_EXPORT_BINDING_ID,
    FIELD_EXPORT_ENTITY_ID,
    FIELD_EXPORT_NAME,
    FIELD_EXPORT_TARGET_ID,
    _devices_schema,
    _heating_mode_schema,
    _is_valid_dhw_on_temperature,
    _valid_scan_interval_or_default,
    _validate_dhw_on_temperature,
    _validate_scan_interval,
    _validate_selected_object_ids,
)
from .heating_config import ZontHeatingModeConfiguration
from .heating_modes import (
    async_discover_heating_modes,
    eligible_off_modes,
    relevant_heating_circuit_ids,
)
from .object_discovery import (
    async_discover_importable_objects_from_requests,
)
from .object_export import (
    ZontExportSourceError,
    ZontTemperatureExportBinding,
    command_response_id,
    export_source_reference,
    export_temperature_command,
    resolve_export_source,
    temperature_export_bindings,
    validate_export_source,
)
from .object_import import (
    ZontImportableObject,
    importable_object_descriptors,
    object_import_configuration,
)
from .objects import (
    OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
    ZontDigitalTemperatureSensorData,
    ZontObject,
)
from .runtime import ZontRuntimeData

_LOGGER = logging.getLogger(__name__)


class _TemperatureExportOptionsFlowSteps:
    """Implement temperature-export steps for the options flow."""

    async def async_step_exports(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show temperature export operations."""
        menu_options = ["export_create_source", "export_link"]
        if temperature_export_bindings(self.config_entry.options):
            menu_options.append("export_manage")
        return self.async_show_menu(
            step_id="exports",
            menu_options=menu_options,
        )

    async def async_step_export_create_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a Home Assistant source for a new ZONT object."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id, error = _validate_export_source_selection(
                self.hass,
                self.config_entry,
                user_input.get(FIELD_EXPORT_ENTITY_ID),
            )
            if error is None:
                assert entity_id is not None
                self._export_source_entity_id = entity_id
                return await self.async_step_export_create()
            errors["base"] = error

        return self.async_show_form(
            step_id="export_create_source",
            data_schema=_export_source_schema(self.hass, self.config_entry),
            errors=errors,
        )

    async def async_step_export_create(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create and bind a new digital temperature sensor in ZONT."""
        entity_id = self._export_source_entity_id
        if entity_id is None:
            return self.async_abort(reason=ERROR_UNKNOWN)

        errors: dict[str, str] = {}
        objects_loaded = await self._async_load_export_objects()
        if not objects_loaded:
            errors["base"] = self._objects_error or ERROR_CANNOT_READ_DEVICES
        elif user_input is not None:
            name = str(user_input.get(FIELD_EXPORT_NAME, "")).strip()
            if not name:
                errors["base"] = ERROR_INVALID_EXPORT_TARGET
            elif any(
                obj.name.casefold() == name.casefold() for obj in self._objects.values()
            ):
                errors["base"] = ERROR_EXPORT_NAME_IN_USE
            else:
                binding, error = await _async_create_temperature_export(
                    self.hass,
                    self.config_entry,
                    entity_id,
                    name,
                )
                if error is None:
                    assert binding is not None
                    return self.async_create_entry(
                        data=_options_with_export_bindings(
                            self.config_entry.options,
                            (
                                *temperature_export_bindings(self.config_entry.options),
                                binding,
                            ),
                        )
                    )
                errors["base"] = error

        state = self.hass.states.get(entity_id)
        default_name = f"HA - {state.name if state is not None else entity_id}"
        return self.async_show_form(
            step_id="export_create",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        FIELD_EXPORT_NAME,
                        default=default_name,
                    ): TextSelector()
                }
            ),
            errors=errors,
            description_placeholders={"source": state.name if state else entity_id},
        )

    async def async_step_export_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an existing type=1 object and a Home Assistant source."""
        if not await self._async_load_export_objects():
            return self.async_show_form(
                step_id="export_link",
                data_schema=vol.Schema({}),
                errors={"base": self._objects_error or ERROR_CANNOT_READ_DEVICES},
            )

        bindings = temperature_export_bindings(self.config_entry.options)
        targets = _available_temperature_targets(self._objects, bindings)
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id, error = _validate_export_source_selection(
                self.hass,
                self.config_entry,
                user_input.get(FIELD_EXPORT_ENTITY_ID),
            )
            target_id = _selected_target_id(
                user_input.get(FIELD_EXPORT_TARGET_ID),
                frozenset(targets),
            )
            if error is not None:
                errors["base"] = error
            elif target_id is None:
                errors["base"] = ERROR_INVALID_EXPORT_TARGET
            else:
                assert entity_id is not None
                self._export_source_entity_id = entity_id
                self._export_target_id = target_id
                return await self.async_step_export_link_confirm()

        return self.async_show_form(
            step_id="export_link",
            data_schema=_export_link_schema(
                self.hass,
                self.config_entry,
                targets,
            ),
            errors=errors,
        )

    async def async_step_export_link_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the first write to an existing ZONT object."""
        entity_id = self._export_source_entity_id
        target_id = self._export_target_id
        target = self._objects.get(target_id) if target_id is not None else None
        if entity_id is None or target_id is None or target is None:
            return self.async_abort(reason=ERROR_UNKNOWN)

        errors: dict[str, str] = {}
        if user_input is not None:
            binding, error = await _async_bind_existing_temperature_export(
                self.hass,
                self.config_entry,
                entity_id,
                target_id,
                target.name,
            )
            if error is None:
                assert binding is not None
                return self.async_create_entry(
                    data=_options_with_export_bindings(
                        self.config_entry.options,
                        (
                            *temperature_export_bindings(self.config_entry.options),
                            binding,
                        ),
                    )
                )
            errors["base"] = error

        source_state = self.hass.states.get(entity_id)
        return self.async_show_form(
            step_id="export_link_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "source": source_state.name if source_state else entity_id,
                "target": target.name,
                "target_id": str(target_id),
            },
        )

    async def async_step_export_manage(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one existing export binding to manage."""
        bindings = temperature_export_bindings(self.config_entry.options)
        if not bindings:
            return await self.async_step_exports()
        errors: dict[str, str] = {}
        if user_input is not None:
            target_id = _selected_target_id(
                user_input.get(FIELD_EXPORT_BINDING_ID),
                frozenset(binding.target_id for binding in bindings),
            )
            if target_id is None:
                errors["base"] = ERROR_INVALID_EXPORT_TARGET
            else:
                self._managed_export_target_id = target_id
                return await self.async_step_export_manage_action()

        return self.async_show_form(
            step_id="export_manage",
            data_schema=_export_manage_schema(self.hass, bindings),
            errors=errors,
        )

    async def async_step_export_manage_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show supported changes for one export binding."""
        if self._managed_binding() is None:
            return self.async_abort(reason=ERROR_UNKNOWN)
        return self.async_show_menu(
            step_id="export_manage_action",
            menu_options=(
                "export_change_source",
                "export_rebind",
                "export_delete",
            ),
        )

    async def async_step_export_change_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the Home Assistant source of one binding."""
        binding = self._managed_binding()
        if binding is None:
            return self.async_abort(reason=ERROR_UNKNOWN)
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id, error = _validate_export_source_selection(
                self.hass,
                self.config_entry,
                user_input.get(FIELD_EXPORT_ENTITY_ID),
                ignored_target_id=binding.target_id,
            )
            if error is None:
                assert entity_id is not None
                updated, error = await _async_bind_existing_temperature_export(
                    self.hass,
                    self.config_entry,
                    entity_id,
                    binding.target_id,
                    binding.target_name,
                    ignored_target_id=binding.target_id,
                )
                if error is None:
                    assert updated is not None
                    _delete_export_issue(self.hass, binding.target_id)
                    return self.async_create_entry(
                        data=_options_replacing_export_binding(
                            self.config_entry.options,
                            binding.target_id,
                            updated,
                        )
                    )
            errors["base"] = error or ERROR_UNKNOWN

        default = er.async_resolve_entity_id(
            er.async_get(self.hass),
            binding.source,
        )
        return self.async_show_form(
            step_id="export_change_source",
            data_schema=_export_source_schema(
                self.hass,
                self.config_entry,
                default=default,
            ),
            errors=errors,
            description_placeholders={
                "target": binding.target_name,
                "target_id": str(binding.target_id),
            },
        )

    async def async_step_export_rebind(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a replacement ZONT target for one binding."""
        binding = self._managed_binding()
        if binding is None:
            return self.async_abort(reason=ERROR_UNKNOWN)
        if not await self._async_load_export_objects():
            return self.async_show_form(
                step_id="export_rebind",
                data_schema=vol.Schema({}),
                errors={"base": self._objects_error or ERROR_CANNOT_READ_DEVICES},
            )

        other_bindings = tuple(
            candidate
            for candidate in temperature_export_bindings(self.config_entry.options)
            if candidate.target_id != binding.target_id
        )
        targets = _available_temperature_targets(self._objects, other_bindings)
        errors: dict[str, str] = {}
        if user_input is not None:
            target_id = _selected_target_id(
                user_input.get(FIELD_EXPORT_TARGET_ID),
                frozenset(targets),
            )
            if target_id is None:
                errors["base"] = ERROR_INVALID_EXPORT_TARGET
            else:
                self._export_target_id = target_id
                return await self.async_step_export_rebind_confirm()

        return self.async_show_form(
            step_id="export_rebind",
            data_schema=_export_target_schema(targets, default=binding.target_id),
            errors=errors,
        )

    async def async_step_export_rebind_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and test a replacement ZONT target."""
        binding = self._managed_binding()
        target_id = self._export_target_id
        target = self._objects.get(target_id) if target_id is not None else None
        if binding is None or target_id is None or target is None:
            return self.async_abort(reason=ERROR_UNKNOWN)

        source_entity_id = er.async_resolve_entity_id(
            er.async_get(self.hass),
            binding.source,
        )
        if source_entity_id is None:
            return self.async_show_form(
                step_id="export_rebind_confirm",
                data_schema=vol.Schema({}),
                errors={"base": ERROR_INVALID_EXPORT_SOURCE},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            updated, error = await _async_bind_existing_temperature_export(
                self.hass,
                self.config_entry,
                source_entity_id,
                target_id,
                target.name,
                ignored_target_id=binding.target_id,
            )
            if error is None:
                assert updated is not None
                _delete_export_issue(self.hass, binding.target_id)
                return self.async_create_entry(
                    data=_options_replacing_export_binding(
                        self.config_entry.options,
                        binding.target_id,
                        updated,
                    )
                )
            errors["base"] = error

        source_state = self.hass.states.get(source_entity_id)
        return self.async_show_form(
            step_id="export_rebind_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "source": source_state.name if source_state else source_entity_id,
                "target": target.name,
                "target_id": str(target_id),
            },
        )

    async def async_step_export_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one binding without deleting its ZONT object."""
        binding = self._managed_binding()
        if binding is None:
            return self.async_abort(reason=ERROR_UNKNOWN)
        if user_input is not None:
            _delete_export_issue(self.hass, binding.target_id)
            remaining = tuple(
                candidate
                for candidate in temperature_export_bindings(self.config_entry.options)
                if candidate.target_id != binding.target_id
            )
            updated_options = _options_with_export_bindings(
                self.config_entry.options,
                remaining,
            )
            excluded = set(updated_options[CONF_EXCLUDED_OBJECT_IDS])
            excluded.add(binding.target_id)
            updated_options[CONF_EXCLUDED_OBJECT_IDS] = sorted(excluded)
            return self.async_create_entry(data=updated_options)
        return self.async_show_form(
            step_id="export_delete",
            data_schema=vol.Schema({}),
            description_placeholders={
                "target": binding.target_name,
                "target_id": str(binding.target_id),
            },
        )

    async def _async_load_export_objects(self) -> bool:
        """Load the current object snapshot once for export flow steps."""
        if not self._objects_loaded:
            self._objects, self._objects_error = await _async_get_entry_objects(
                self.hass,
                self.config_entry,
            )
            self._objects_loaded = True
        return self._objects_error is None

    def _managed_binding(self) -> ZontTemperatureExportBinding | None:
        """Return the binding selected by the manage flow."""
        return next(
            (
                binding
                for binding in temperature_export_bindings(self.config_entry.options)
                if binding.target_id == self._managed_export_target_id
            ),
            None,
        )


def _validate_export_source_selection(
    hass: HomeAssistant,
    entry: ConfigEntry,
    value: Any,
    *,
    ignored_target_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Validate one selected source and reject duplicate bindings."""
    if not isinstance(value, str):
        return None, ERROR_INVALID_EXPORT_SOURCE
    entity_id = value.strip()
    if not entity_id:
        return None, ERROR_INVALID_EXPORT_SOURCE
    try:
        validate_export_source(hass, entity_id, entry.entry_id)
    except ZontExportSourceError:
        return None, ERROR_INVALID_EXPORT_SOURCE

    source = export_source_reference(hass, entity_id)
    if any(
        binding.target_id != ignored_target_id
        and (
            binding.source == source
            or resolve_export_source(hass, binding.source) == entity_id
        )
        for binding in temperature_export_bindings(entry.options)
    ):
        return None, ERROR_DUPLICATE_EXPORT_SOURCE
    return entity_id, None


async def _async_create_temperature_export(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entity_id: str,
    name: str,
) -> tuple[ZontTemperatureExportBinding | None, str | None]:
    """Create, verify and describe one new ZONT temperature target."""
    entity_id, error = _validate_export_source_selection(hass, entry, entity_id)
    if error is not None or entity_id is None:
        return None, error or ERROR_INVALID_EXPORT_SOURCE
    client = _loaded_entry_client(entry)
    if client is None:
        return None, ERROR_CANNOT_CONNECT
    try:
        value = validate_export_source(hass, entity_id, entry.entry_id)
        response = await client.async_send_named_command(
            name,
            OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
            export_temperature_command(value),
        )
        target_id = command_response_id(response)
        if target_id is None or response.get("cmdres") != 0:
            return None, ERROR_EXPORT_COMMAND_REJECTED
        if any(
            binding.target_id == target_id
            for binding in temperature_export_bindings(entry.options)
        ):
            return None, ERROR_DUPLICATE_EXPORT_TARGET
        state = await client.async_get_object_state(target_id)
    except ZontExportSourceError:
        return None, ERROR_INVALID_EXPORT_SOURCE
    except (ZontConnectionError, ZontRequestTimeoutError):
        return None, ERROR_CANNOT_CONNECT
    except ZontProtocolError:
        return None, ERROR_INVALID_EXPORT_TARGET

    object_type = state.get("type")
    if (
        state.get("failed")
        or type(object_type) is not int
        or object_type != OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR
    ):
        return None, ERROR_INVALID_EXPORT_TARGET
    target_name = state.get("name")
    return (
        ZontTemperatureExportBinding(
            export_source_reference(hass, entity_id),
            target_id,
            target_name.strip()
            if isinstance(target_name, str) and target_name.strip()
            else name,
        ),
        None,
    )


async def _async_bind_existing_temperature_export(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entity_id: str,
    target_id: int,
    target_name: str,
    *,
    ignored_target_id: int | None = None,
) -> tuple[ZontTemperatureExportBinding | None, str | None]:
    """Test and return one explicit existing-object binding."""
    entity_id, error = _validate_export_source_selection(
        hass,
        entry,
        entity_id,
        ignored_target_id=ignored_target_id,
    )
    if error is not None or entity_id is None:
        return None, error or ERROR_INVALID_EXPORT_SOURCE
    if any(
        binding.target_id == target_id and binding.target_id != ignored_target_id
        for binding in temperature_export_bindings(entry.options)
    ):
        return None, ERROR_DUPLICATE_EXPORT_TARGET

    client = _loaded_entry_client(entry)
    if client is None:
        return None, ERROR_CANNOT_CONNECT
    try:
        state = await client.async_get_object_state(target_id)
        object_type = state.get("type")
        if (
            state.get("failed")
            or type(object_type) is not int
            or object_type != OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR
        ):
            return None, ERROR_INVALID_EXPORT_TARGET
        value = validate_export_source(hass, entity_id, entry.entry_id)
        response = await client.async_send_command(
            target_id,
            export_temperature_command(value),
        )
    except ZontExportSourceError:
        return None, ERROR_INVALID_EXPORT_SOURCE
    except (ZontConnectionError, ZontRequestTimeoutError):
        return None, ERROR_CANNOT_CONNECT
    except ZontProtocolError:
        return None, ERROR_INVALID_EXPORT_TARGET
    if (
        command_response_id(response) != target_id
        or type(response.get("cmdres")) is not int
        or response["cmdres"] != 0
    ):
        return None, ERROR_EXPORT_COMMAND_REJECTED

    current_name = state.get("name")
    return (
        ZontTemperatureExportBinding(
            export_source_reference(hass, entity_id),
            target_id,
            current_name.strip()
            if isinstance(current_name, str) and current_name.strip()
            else target_name,
        ),
        None,
    )


def _loaded_entry_client(entry: ConfigEntry) -> Any | None:
    """Return the existing connected client; exports never open a second socket."""
    if entry.state is not ConfigEntryState.LOADED:
        return None
    runtime_data = cast(ZontRuntimeData, entry.runtime_data)
    return runtime_data.client if runtime_data.client.is_connected else None


def _available_temperature_targets(
    objects: dict[int, ZontObject],
    bindings: tuple[ZontTemperatureExportBinding, ...],
) -> dict[int, ZontDigitalTemperatureSensorData]:
    """Return type=1 targets not already owned by another export."""
    used = {binding.target_id for binding in bindings}
    return {
        object_id: obj
        for object_id, obj in objects.items()
        if isinstance(obj, ZontDigitalTemperatureSensorData) and object_id not in used
    }


def _selected_target_id(value: Any, valid_ids: frozenset[int]) -> int | None:
    """Validate one select-selector object ID."""
    if isinstance(value, bool):
        return None
    try:
        object_id = int(value)
    except (TypeError, ValueError):
        return None
    return (
        object_id if str(object_id) == str(value) and object_id in valid_ids else None
    )


def _export_source_schema(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    default: str | None = None,
) -> vol.Schema:
    """Return a temperature-only source selector excluding this integration."""
    excluded_entities = [
        registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass),
            entry.entry_id,
        )
    ]
    marker: vol.Marker = vol.Required(FIELD_EXPORT_ENTITY_ID)
    if default is not None:
        marker = vol.Required(FIELD_EXPORT_ENTITY_ID, default=default)
    return vol.Schema(
        {
            marker: EntitySelector(
                EntitySelectorConfig(
                    domain="sensor",
                    device_class=SensorDeviceClass.TEMPERATURE,
                    exclude_entities=excluded_entities,
                )
            )
        }
    )


def _export_target_schema(
    targets: dict[int, ZontDigitalTemperatureSensorData],
    *,
    default: int | None = None,
) -> vol.Schema:
    """Return a selector for existing type=1 ZONT objects."""
    options = [
        SelectOptionDict(
            value=str(obj.object_id),
            label=f"Цифровой датчик температуры - {obj.name} (ID {obj.object_id})",
        )
        for obj in sorted(
            targets.values(),
            key=lambda candidate: (candidate.name.casefold(), candidate.object_id),
        )
    ]
    marker: vol.Marker = vol.Required(FIELD_EXPORT_TARGET_ID)
    if default is not None and default in targets:
        marker = vol.Required(FIELD_EXPORT_TARGET_ID, default=str(default))
    return vol.Schema(
        {
            marker: SelectSelector(
                SelectSelectorConfig(options=options, custom_value=False)
            )
        }
    )


def _export_link_schema(
    hass: HomeAssistant,
    entry: ConfigEntry,
    targets: dict[int, ZontDigitalTemperatureSensorData],
) -> vol.Schema:
    """Return source and existing-target selectors in one form."""
    source_schema = _export_source_schema(hass, entry)
    target_schema = _export_target_schema(targets)
    return source_schema.extend(target_schema.schema)


def _export_manage_schema(
    hass: HomeAssistant,
    bindings: tuple[ZontTemperatureExportBinding, ...],
) -> vol.Schema:
    """Return a selector for one configured export binding."""
    registry = er.async_get(hass)
    options: list[SelectOptionDict] = []
    for binding in bindings:
        entity_id = er.async_resolve_entity_id(registry, binding.source)
        state = hass.states.get(entity_id) if entity_id is not None else None
        source_name = (
            state.name if state is not None else entity_id or "Источник недоступен"
        )
        options.append(
            SelectOptionDict(
                value=str(binding.target_id),
                label=(
                    f"{source_name} → {binding.target_name} (ID {binding.target_id})"
                ),
            )
        )
    return vol.Schema(
        {
            vol.Required(FIELD_EXPORT_BINDING_ID): SelectSelector(
                SelectSelectorConfig(options=options, custom_value=False)
            )
        }
    )


def _options_with_export_bindings(
    options: Mapping[str, Any],
    bindings: tuple[ZontTemperatureExportBinding, ...],
) -> dict[str, Any]:
    """Store bindings and keep every used or orphaned target excluded."""
    configuration = object_import_configuration(options)
    target_ids = {binding.target_id for binding in bindings}
    reserved_ids = configuration.exported_ids | target_ids
    return {
        **options,
        CONF_TEMPERATURE_EXPORTS: [binding.as_dict() for binding in bindings],
        CONF_IMPORTED_OBJECT_IDS: sorted(configuration.imported_ids - reserved_ids),
        CONF_EXCLUDED_OBJECT_IDS: sorted(configuration.excluded_ids | reserved_ids),
        CONF_AUTO_IMPORT_NEW_OBJECTS: configuration.auto_import_new,
    }


def _options_replacing_export_binding(
    options: Mapping[str, Any],
    old_target_id: int,
    replacement: ZontTemperatureExportBinding,
) -> dict[str, Any]:
    """Replace one binding while preserving the old target exclusion."""
    bindings = tuple(
        replacement if binding.target_id == old_target_id else binding
        for binding in temperature_export_bindings(options)
    )
    updated = _options_with_export_bindings(options, bindings)
    excluded = set(updated[CONF_EXCLUDED_OBJECT_IDS])
    excluded.add(old_target_id)
    updated[CONF_EXCLUDED_OBJECT_IDS] = sorted(excluded)
    return updated


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


@callback
def _delete_export_issue(hass: HomeAssistant, target_id: int) -> None:
    """Remove a repair issue belonging to a deleted or replaced binding."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"temperature_export_{target_id}",
    )


async def _async_discover_off_modes(
    hass: HomeAssistant,
    data: dict[str, str],
) -> tuple[tuple[ZontHeatingModeConfiguration, ...], str | None]:
    """Return modes proven to disable all supported heating circuits."""
    try:
        discovery = await async_discover_heating_modes(
            async_get_clientsession(hass),
            controller_websocket_url(data[CONF_HOST]),
            ZontCredentials(
                username=data[CONF_USERNAME],
                password=data[CONF_PASSWORD],
            ),
        )
    except ZontAuthenticationError:
        return (), ERROR_INVALID_AUTH
    except ZontConnectionError:
        return (), ERROR_CANNOT_CONNECT
    except ZontProtocolError:
        _LOGGER.debug("Unable to discover ZONT heating modes", exc_info=True)
        return (), ERROR_CANNOT_READ_MODES
    except Exception:
        _LOGGER.exception("Unexpected error while discovering ZONT heating modes")
        return (), ERROR_UNKNOWN
    return discovery.eligible_off_modes, None


async def _async_discover_objects(
    hass: HomeAssistant,
    data: dict[str, str],
) -> tuple[dict[int, ZontObject], str | None]:
    """Discover importable objects using one bounded temporary connection."""
    try:
        async with async_open_temporary_request_session(
            async_get_clientsession(hass),
            controller_websocket_url(data[CONF_HOST]),
            ZontCredentials(
                username=data[CONF_USERNAME],
                password=data[CONF_PASSWORD],
            ),
        ) as requests:
            objects = await async_discover_importable_objects_from_requests(requests)
    except ZontAuthenticationError:
        return {}, ERROR_INVALID_AUTH
    except ZontConnectionError:
        return {}, ERROR_CANNOT_CONNECT
    except ZontProtocolError:
        _LOGGER.debug("Unable to discover ZONT objects", exc_info=True)
        return {}, ERROR_CANNOT_READ_DEVICES
    except Exception:
        _LOGGER.exception("Unexpected error while discovering ZONT objects")
        return {}, ERROR_UNKNOWN
    return dict(objects), None


async def _async_get_entry_off_modes(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> tuple[tuple[ZontHeatingModeConfiguration, ...], str | None]:
    """Return off modes without opening a competing controller connection."""
    if entry.state is not ConfigEntryState.LOADED:
        return await _async_discover_off_modes(
            hass,
            {
                CONF_HOST: entry.data[CONF_HOST],
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_PASSWORD: entry.data[CONF_PASSWORD],
            },
        )

    runtime_data = cast(ZontRuntimeData, entry.runtime_data)
    if not runtime_data.client.is_connected:
        return (), ERROR_CANNOT_CONNECT

    coordinator = runtime_data.coordinator
    if not _heating_mode_data_is_complete(coordinator.data):
        await coordinator.async_request_refresh()
        if not coordinator.last_update_success:
            return (), ERROR_CANNOT_CONNECT

    data = coordinator.data
    if not _heating_mode_data_is_complete(data):
        return (), ERROR_CANNOT_READ_MODES
    return eligible_off_modes(
        data.objects,
        data.heating_states,
        data.heating_modes,
    ), None


async def _async_get_entry_objects(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> tuple[dict[int, ZontObject], str | None]:
    """Return current objects without competing with a loaded entry connection."""
    if entry.state is not ConfigEntryState.LOADED:
        return await _async_discover_objects(
            hass,
            {
                CONF_HOST: entry.data[CONF_HOST],
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_PASSWORD: entry.data[CONF_PASSWORD],
            },
        )

    runtime_data = cast(ZontRuntimeData, entry.runtime_data)
    coordinator = runtime_data.coordinator
    cached_objects = dict(coordinator.data.objects)
    if not runtime_data.client.is_connected:
        return (cached_objects, None) if cached_objects else ({}, ERROR_CANNOT_CONNECT)

    try:
        await coordinator.async_request_refresh()
    except Exception:
        _LOGGER.debug(
            "Unable to refresh ZONT objects for the options flow",
            exc_info=True,
        )
    if coordinator.last_update_success:
        return dict(coordinator.data.objects), None
    if cached_objects:
        return cached_objects, None
    return {}, ERROR_CANNOT_CONNECT


def _heating_mode_data_is_complete(data: ZontData) -> bool:
    """Return whether a coordinator snapshot can validate an off mode."""
    circuit_ids = relevant_heating_circuit_ids(data.objects)
    return bool(
        circuit_ids and data.heating_modes and circuit_ids.issubset(data.heating_states)
    )
