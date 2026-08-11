"""Controller and child-object device registry management."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_AUTO_TITLE, CONF_CONTROLLER, DOMAIN
from .object_descriptions import object_device_identifier
from .object_import import importable_object_descriptor, object_import_configuration
from .presentation import controller_device_name, controller_entry_title
from .protocol.controller import (
    ZontControllerInfo,
    controller_configuration_url,
)
from .runtime import ZontRuntimeData

type ZontDeviceConfigEntry = ConfigEntry[ZontRuntimeData]


@callback
def async_sync_object_devices(
    hass: HomeAssistant,
    entry: ZontDeviceConfigEntry,
) -> None:
    """Create or update devices represented by discovered ZONT objects."""
    controller_identifier = entry.unique_id or entry.entry_id
    device_registry = dr.async_get(hass)
    import_configuration = object_import_configuration(entry.options)
    for obj in entry.runtime_data.coordinator.data.objects.values():
        descriptor = importable_object_descriptor(obj)
        if descriptor is None or not import_configuration.imports(obj.object_id):
            continue
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={
                (
                    DOMAIN,
                    object_device_identifier(controller_identifier, obj.object_id),
                )
            },
            name=obj.name,
            manufacturer=descriptor.manufacturer,
            model=descriptor.model,
            via_device=(DOMAIN, controller_identifier),
        )


@callback
def async_cleanup_excluded_object_devices(
    hass: HomeAssistant,
    entry: ZontDeviceConfigEntry,
    controller_identifier: str,
) -> None:
    """Remove registry entries for objects explicitly excluded by the user."""
    configuration = object_import_configuration(entry.options)
    if configuration.legacy_import_all:
        return

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    identifier_prefix = f"{controller_identifier}:object:"
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        object_id = _object_id_from_device(device, identifier_prefix)
        if object_id is None or configuration.imports(object_id):
            continue
        for entity in er.async_entries_for_device(
            entity_registry,
            device.id,
            include_disabled_entities=True,
        ):
            if entity.config_entry_id == entry.entry_id:
                entity_registry.async_remove(entity.entity_id)
        device_registry.async_remove_device(device.id)


def _object_id_from_device(
    device: dr.DeviceEntry,
    identifier_prefix: str,
) -> int | None:
    """Extract a child object ID from one device registry entry."""
    for domain, identifier in device.identifiers:
        if domain != DOMAIN or not identifier.startswith(identifier_prefix):
            continue
        suffix = identifier.removeprefix(identifier_prefix)
        if suffix.isdecimal():
            return int(suffix)
    return None


@callback
def async_apply_controller_info(
    hass: HomeAssistant,
    entry: ZontDeviceConfigEntry,
    device_id: str,
    info: ZontControllerInfo,
) -> None:
    """Persist refreshed controller data and update its registry entry."""
    title = controller_entry_title(info, entry.data[CONF_HOST])
    previous_auto_title = entry.data.get(CONF_AUTO_TITLE)
    title_is_managed = entry.title == previous_auto_title
    data = dict(entry.data)
    data[CONF_CONTROLLER] = info.as_dict()
    data[CONF_AUTO_TITLE] = title
    updated_title = title if title_is_managed else entry.title
    if data != entry.data or updated_title != entry.title:
        hass.config_entries.async_update_entry(entry, data=data, title=updated_title)

    dr.async_get(hass).async_update_device(
        device_id,
        name=controller_device_name(info),
        manufacturer="ZONT",
        model=info.model,
        model_id=info.board_model,
        sw_version=info.firmware_version,
        serial_number=info.serial_number,
        configuration_url=controller_configuration_url(entry.data[CONF_HOST]),
    )
