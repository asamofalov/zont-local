"""Entity export steps for the ZONT options flow."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from ..const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_EXPORTS,
    CONF_IMPORTED_OBJECT_IDS,
    DOMAIN,
)
from ..export import (
    OPENING_DEVICE_CLASSES,
    ZontExportBinding,
    ZontExportKind,
    ZontExportSourceError,
    command_response_id,
    export_bindings,
    export_command,
    export_source_reference,
    export_target_matches,
    export_target_protocol_identity,
    resolve_export_source,
    validate_export_source,
)
from ..object_import import object_import_configuration
from ..protocol import (
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from ..protocol.objects import (
    ANALOG_INPUT_SUBTYPE_DISCRETE_NC,
    ZontAnalogInputData,
    ZontDigitalTemperatureSensorData,
    ZontObject,
)
from ..runtime import ZontRuntimeData
from .discovery import _async_get_entry_objects
from .schemas import (
    ERROR_CANNOT_CONNECT,
    ERROR_CANNOT_READ_DEVICES,
    ERROR_DUPLICATE_EXPORT_SOURCE,
    ERROR_DUPLICATE_EXPORT_TARGET,
    ERROR_EXPORT_COMMAND_REJECTED,
    ERROR_EXPORT_NAME_IN_USE,
    ERROR_INVALID_EXPORT_SOURCE,
    ERROR_INVALID_EXPORT_TARGET,
    ERROR_UNKNOWN,
    FIELD_EXPORT_BINDING_ID,
    FIELD_EXPORT_ENTITY_ID,
    FIELD_EXPORT_NAME,
    FIELD_EXPORT_TARGET_ID,
)

_LOGGER = logging.getLogger(__name__)

_EXPORT_KIND_LABELS = {
    ZontExportKind.TEMPERATURE: "Температура",
    ZontExportKind.OPENING: "Открытие",
}


class _ExportOptionsFlowSteps:
    """Implement entity-export steps for the options flow."""

    async def async_step_exports(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show export operations."""
        menu_options = ["export_create_source", "export_link"]
        if export_bindings(self.config_entry.options):
            menu_options.append("export_manage")
        return self.async_show_menu(step_id="exports", menu_options=menu_options)

    async def async_step_export_create_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a Home Assistant source for a new ZONT object."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id, kind, error = _validate_export_source_selection(
                self.hass,
                self.config_entry,
                user_input.get(FIELD_EXPORT_ENTITY_ID),
            )
            if error is None:
                assert entity_id is not None and kind is not None
                self._export_source_entity_id = entity_id
                self._export_source_kind = kind
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
        """Create and bind a compatible ZONT object."""
        entity_id = self._export_source_entity_id
        kind = self._export_source_kind
        if entity_id is None or kind is None:
            return self.async_abort(reason=ERROR_UNKNOWN)

        errors: dict[str, str] = {}
        if not await self._async_load_export_objects():
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
                binding, error = await _async_create_export(
                    self.hass,
                    self.config_entry,
                    entity_id,
                    kind,
                    name,
                )
                if error is None:
                    assert binding is not None
                    return self.async_create_entry(
                        data=_options_with_export_bindings(
                            self.config_entry.options,
                            (*export_bindings(self.config_entry.options), binding),
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
            description_placeholders={
                "source": state.name if state else entity_id,
                "kind": _EXPORT_KIND_LABELS[kind],
            },
        )

    async def async_step_export_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a Home Assistant source for an existing ZONT object."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id, kind, error = _validate_export_source_selection(
                self.hass,
                self.config_entry,
                user_input.get(FIELD_EXPORT_ENTITY_ID),
            )
            if error is None:
                assert entity_id is not None and kind is not None
                self._export_source_entity_id = entity_id
                self._export_source_kind = kind
                return await self.async_step_export_link_target()
            errors["base"] = error

        return self.async_show_form(
            step_id="export_link",
            data_schema=_export_source_schema(self.hass, self.config_entry),
            errors=errors,
        )

    async def async_step_export_link_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an existing ZONT target compatible with the source."""
        kind = self._export_source_kind
        if self._export_source_entity_id is None or kind is None:
            return self.async_abort(reason=ERROR_UNKNOWN)
        if not await self._async_load_export_objects():
            return self.async_show_form(
                step_id="export_link_target",
                data_schema=vol.Schema({}),
                errors={"base": self._objects_error or ERROR_CANNOT_READ_DEVICES},
            )

        targets = _available_targets(
            self._objects,
            export_bindings(self.config_entry.options),
            kind,
        )
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
                return await self.async_step_export_link_confirm()

        return self.async_show_form(
            step_id="export_link_target",
            data_schema=_export_target_schema(targets, kind),
            errors=errors,
            description_placeholders={"kind": _EXPORT_KIND_LABELS[kind]},
        )

    async def async_step_export_link_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the first write to an existing ZONT object."""
        entity_id = self._export_source_entity_id
        kind = self._export_source_kind
        target_id = self._export_target_id
        target = self._objects.get(target_id) if target_id is not None else None
        if entity_id is None or kind is None or target_id is None or target is None:
            return self.async_abort(reason=ERROR_UNKNOWN)

        errors: dict[str, str] = {}
        if user_input is not None:
            binding, error = await _async_bind_existing_export(
                self.hass,
                self.config_entry,
                entity_id,
                kind,
                target_id,
                target.name,
            )
            if error is None:
                assert binding is not None
                return self.async_create_entry(
                    data=_options_with_export_bindings(
                        self.config_entry.options,
                        (*export_bindings(self.config_entry.options), binding),
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
        bindings = export_bindings(self.config_entry.options)
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
            entity_id, _, error = _validate_export_source_selection(
                self.hass,
                self.config_entry,
                user_input.get(FIELD_EXPORT_ENTITY_ID),
                expected_kind=binding.kind,
                ignored_target_id=binding.target_id,
            )
            if error is None:
                assert entity_id is not None
                updated, error = await _async_bind_existing_export(
                    self.hass,
                    self.config_entry,
                    entity_id,
                    binding.kind,
                    binding.target_id,
                    binding.target_name,
                    ignored_target_id=binding.target_id,
                )
                if error is None:
                    assert updated is not None
                    _delete_export_issue(self.hass, binding)
                    return self.async_create_entry(
                        data=_options_replacing_export_binding(
                            self.config_entry.options,
                            binding.target_id,
                            updated,
                        )
                    )
            errors["base"] = error or ERROR_UNKNOWN

        default = er.async_resolve_entity_id(er.async_get(self.hass), binding.source)
        return self.async_show_form(
            step_id="export_change_source",
            data_schema=_export_source_schema(
                self.hass,
                self.config_entry,
                kind=binding.kind,
                default=default,
            ),
            errors=errors,
            description_placeholders={
                "target": binding.target_name,
                "target_id": str(binding.target_id),
                "kind": _EXPORT_KIND_LABELS[binding.kind],
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
            for candidate in export_bindings(self.config_entry.options)
            if candidate.target_id != binding.target_id
        )
        targets = _available_targets(self._objects, other_bindings, binding.kind)
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
            data_schema=_export_target_schema(
                targets,
                binding.kind,
                default=binding.target_id,
            ),
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
            er.async_get(self.hass), binding.source
        )
        if source_entity_id is None:
            return self.async_show_form(
                step_id="export_rebind_confirm",
                data_schema=vol.Schema({}),
                errors={"base": ERROR_INVALID_EXPORT_SOURCE},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            updated, error = await _async_bind_existing_export(
                self.hass,
                self.config_entry,
                source_entity_id,
                binding.kind,
                target_id,
                target.name,
                ignored_target_id=binding.target_id,
            )
            if error is None:
                assert updated is not None
                _delete_export_issue(self.hass, binding)
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
            _delete_export_issue(self.hass, binding)
            remaining = tuple(
                candidate
                for candidate in export_bindings(self.config_entry.options)
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

    def _managed_binding(self) -> ZontExportBinding | None:
        """Return the binding selected by the manage flow."""
        return next(
            (
                binding
                for binding in export_bindings(self.config_entry.options)
                if binding.target_id == self._managed_export_target_id
            ),
            None,
        )


def _validate_export_source_selection(
    hass: HomeAssistant,
    entry: ConfigEntry,
    value: Any,
    *,
    expected_kind: ZontExportKind | None = None,
    ignored_target_id: int | None = None,
) -> tuple[str | None, ZontExportKind | None, str | None]:
    """Validate one selected source and reject duplicate bindings."""
    if not isinstance(value, str) or not (entity_id := value.strip()):
        return None, None, ERROR_INVALID_EXPORT_SOURCE
    try:
        kind, _ = validate_export_source(
            hass,
            entity_id,
            entry.entry_id,
            expected_kind,
        )
    except ZontExportSourceError:
        return None, None, ERROR_INVALID_EXPORT_SOURCE

    source = export_source_reference(hass, entity_id)
    if any(
        binding.target_id != ignored_target_id
        and (
            binding.source == source
            or resolve_export_source(hass, binding.source) == entity_id
        )
        for binding in export_bindings(entry.options)
    ):
        return None, None, ERROR_DUPLICATE_EXPORT_SOURCE
    return entity_id, kind, None


async def _async_create_export(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entity_id: str,
    kind: ZontExportKind,
    name: str,
) -> tuple[ZontExportBinding | None, str | None]:
    """Create, verify and describe one new ZONT export target."""
    entity_id, _, error = _validate_export_source_selection(
        hass,
        entry,
        entity_id,
        expected_kind=kind,
    )
    if error is not None or entity_id is None:
        return None, error or ERROR_INVALID_EXPORT_SOURCE
    client = _loaded_entry_client(entry)
    if client is None:
        return None, ERROR_CANNOT_CONNECT
    try:
        _, value = validate_export_source(hass, entity_id, entry.entry_id, kind)
        object_type, object_subtype = export_target_protocol_identity(kind)
        command = export_command(kind, value)
        if object_subtype is None:
            response = await client.async_send_named_command(
                name,
                object_type,
                command,
            )
        else:
            response = await client.async_send_named_command(
                name,
                object_type,
                command,
                object_subtype=object_subtype,
            )
        target_id = command_response_id(response)
        if (
            target_id is None
            or type(response.get("cmdres")) is not int
            or response["cmdres"] != 0
        ):
            return None, ERROR_EXPORT_COMMAND_REJECTED
        if any(
            binding.target_id == target_id for binding in export_bindings(entry.options)
        ):
            return None, ERROR_DUPLICATE_EXPORT_TARGET
        state = await client.async_get_object_state(target_id)
    except ZontExportSourceError:
        return None, ERROR_INVALID_EXPORT_SOURCE
    except (ZontConnectionError, ZontRequestTimeoutError):
        return None, ERROR_CANNOT_CONNECT
    except ZontProtocolError:
        return None, ERROR_INVALID_EXPORT_TARGET

    if not export_target_matches(kind, state):
        return None, ERROR_INVALID_EXPORT_TARGET
    target_name = state.get("name")
    return (
        ZontExportBinding(
            kind,
            export_source_reference(hass, entity_id),
            target_id,
            target_name.strip()
            if isinstance(target_name, str) and target_name.strip()
            else name,
        ),
        None,
    )


async def _async_bind_existing_export(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entity_id: str,
    kind: ZontExportKind,
    target_id: int,
    target_name: str,
    *,
    ignored_target_id: int | None = None,
) -> tuple[ZontExportBinding | None, str | None]:
    """Test and return one explicit existing-object binding."""
    entity_id, _, error = _validate_export_source_selection(
        hass,
        entry,
        entity_id,
        expected_kind=kind,
        ignored_target_id=ignored_target_id,
    )
    if error is not None or entity_id is None:
        return None, error or ERROR_INVALID_EXPORT_SOURCE
    if any(
        binding.target_id == target_id and binding.target_id != ignored_target_id
        for binding in export_bindings(entry.options)
    ):
        return None, ERROR_DUPLICATE_EXPORT_TARGET

    client = _loaded_entry_client(entry)
    if client is None:
        return None, ERROR_CANNOT_CONNECT
    try:
        state = await client.async_get_object_state(target_id)
        if not export_target_matches(kind, state):
            return None, ERROR_INVALID_EXPORT_TARGET
        _, value = validate_export_source(hass, entity_id, entry.entry_id, kind)
        response = await client.async_send_command(
            target_id,
            export_command(kind, value),
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
        ZontExportBinding(
            kind,
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


def _available_targets(
    objects: dict[int, ZontObject],
    bindings: tuple[ZontExportBinding, ...],
    kind: ZontExportKind,
) -> dict[int, ZontObject]:
    """Return compatible targets not already owned by another export."""
    used = {binding.target_id for binding in bindings}
    if kind is ZontExportKind.TEMPERATURE:
        return {
            object_id: obj
            for object_id, obj in objects.items()
            if isinstance(obj, ZontDigitalTemperatureSensorData)
            and object_id not in used
        }
    return {
        object_id: obj
        for object_id, obj in objects.items()
        if isinstance(obj, ZontAnalogInputData)
        and obj.subtype == ANALOG_INPUT_SUBTYPE_DISCRETE_NC
        and object_id not in used
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
    kind: ZontExportKind | None = None,
    default: str | None = None,
) -> vol.Schema:
    """Return a supported-source selector excluding this integration."""
    excluded_entities = [
        registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass),
            entry.entry_id,
        )
    ]
    filters: list[dict[str, Any]] = []
    if kind in (None, ZontExportKind.TEMPERATURE):
        filters.append(
            {"domain": "sensor", "device_class": SensorDeviceClass.TEMPERATURE}
        )
    if kind in (None, ZontExportKind.OPENING):
        filters.append(
            {
                "domain": "binary_sensor",
                "device_class": sorted(
                    device_class.value for device_class in OPENING_DEVICE_CLASSES
                ),
            }
        )
    marker: vol.Marker = vol.Required(FIELD_EXPORT_ENTITY_ID)
    if default is not None:
        marker = vol.Required(FIELD_EXPORT_ENTITY_ID, default=default)
    return vol.Schema(
        {
            marker: EntitySelector(
                EntitySelectorConfig(
                    filter=filters,
                    exclude_entities=excluded_entities,
                )
            )
        }
    )


def _export_target_schema(
    targets: dict[int, ZontObject],
    kind: ZontExportKind,
    *,
    default: int | None = None,
) -> vol.Schema:
    """Return a selector for compatible existing ZONT objects."""
    target_type = (
        "Цифровой датчик температуры"
        if kind is ZontExportKind.TEMPERATURE
        else "Дискретный вход НЗ"
    )
    options = [
        SelectOptionDict(
            value=str(obj.object_id),
            label=f"{target_type} - {obj.name} (ID {obj.object_id})",
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


def _export_manage_schema(
    hass: HomeAssistant,
    bindings: tuple[ZontExportBinding, ...],
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
                    f"{_EXPORT_KIND_LABELS[binding.kind]}: {source_name} → "
                    f"{binding.target_name} (ID {binding.target_id})"
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
    bindings: tuple[ZontExportBinding, ...],
) -> dict[str, Any]:
    """Store bindings and keep every used or orphaned target excluded."""
    configuration = object_import_configuration(options)
    target_ids = {binding.target_id for binding in bindings}
    reserved_ids = configuration.exported_ids | target_ids
    return {
        **options,
        CONF_EXPORTS: [binding.as_dict() for binding in bindings],
        CONF_IMPORTED_OBJECT_IDS: sorted(configuration.imported_ids - reserved_ids),
        CONF_EXCLUDED_OBJECT_IDS: sorted(configuration.excluded_ids | reserved_ids),
        CONF_AUTO_IMPORT_NEW_OBJECTS: configuration.auto_import_new,
    }


def _options_replacing_export_binding(
    options: Mapping[str, Any],
    old_target_id: int,
    replacement: ZontExportBinding,
) -> dict[str, Any]:
    """Replace one binding while preserving the old target exclusion."""
    bindings = tuple(
        replacement if binding.target_id == old_target_id else binding
        for binding in export_bindings(options)
    )
    updated = _options_with_export_bindings(options, bindings)
    excluded = set(updated[CONF_EXCLUDED_OBJECT_IDS])
    excluded.add(old_target_id)
    updated[CONF_EXCLUDED_OBJECT_IDS] = sorted(excluded)
    return updated


def _delete_export_issue(hass: HomeAssistant, binding: ZontExportBinding) -> None:
    """Remove a repair issue belonging to a deleted or replaced binding."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"{binding.kind.value}_export_{binding.target_id}",
    )
