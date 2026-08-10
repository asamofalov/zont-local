"""ZONT WebSocket integration."""

from __future__ import annotations

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    ZontWsClient,
)
from .const import (
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .controller import (
    ZontControllerInfo,
    controller_configuration_url,
    controller_device_name,
    controller_entry_title,
    controller_websocket_url,
)
from .coordinator import ZontDataUpdateCoordinator, ZontRuntimeData
from .object_export import ZontTemperatureExportManager
from .object_import import (
    importable_object_descriptor,
    object_import_configuration,
)
from .objects import object_device_identifier
from .services import async_setup_services

type ZontConfigEntry = ConfigEntry[ZontRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level service actions."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Reject entries from versions that require adding the controller again."""
    return entry.version == CONFIG_ENTRY_VERSION


async def async_setup_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Set up ZONT from a config entry."""
    controller_info = ZontControllerInfo.from_mapping(entry.data.get(CONF_CONTROLLER))
    if controller_info is None and entry.unique_id is not None:
        controller_info = ZontControllerInfo.from_mapping(
            {"serial_number": entry.unique_id}
        )
    controller_identifier = entry.unique_id or entry.entry_id
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, controller_identifier)},
        name=controller_device_name(controller_info),
        manufacturer="ZONT",
        model=controller_info.model if controller_info is not None else None,
        model_id=(controller_info.board_model if controller_info is not None else None),
        sw_version=(
            controller_info.firmware_version if controller_info is not None else None
        ),
        serial_number=(
            controller_info.serial_number if controller_info is not None else None
        ),
        configuration_url=controller_configuration_url(entry.data[CONF_HOST]),
    )
    _async_cleanup_excluded_object_devices(
        hass,
        entry,
        controller_identifier,
    )

    client = ZontWsClient(
        hass=hass,
        session=async_get_clientsession(hass),
        url=controller_websocket_url(entry.data[CONF_HOST]),
        credentials=ZontCredentials(
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
        ),
        entry_id=entry.entry_id,
        device_id=device.id,
        on_authentication_error=lambda: entry.async_start_reauth(hass),
    )

    try:
        await client.async_start(entry)
    except ZontAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
        ) from err
    except (ZontConnectionError, ZontProtocolError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err

    coordinator = ZontDataUpdateCoordinator(
        hass,
        entry,
        client,
        controller_info,
        lambda info: _async_apply_controller_info(
            hass,
            entry,
            device.id,
            info,
        ),
    )
    export_manager = ZontTemperatureExportManager(hass, entry, client)
    entry.runtime_data = ZontRuntimeData(
        client=client,
        coordinator=coordinator,
        export_manager=export_manager,
        options=dict(entry.options),
        connection_settings=_connection_settings(entry),
        controller_device_id=device.id,
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _async_sync_object_devices(
                hass,
                entry,
                device.id,
            )
        )
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        try:
            await entry.runtime_data.object_entities.async_shutdown()
        finally:
            try:
                await export_manager.async_shutdown()
            finally:
                try:
                    await coordinator.async_shutdown()
                finally:
                    await client.async_stop()
        raise

    export_manager.async_start()
    coordinator.async_start()

    return True


@callback
def _async_sync_object_devices(
    hass: HomeAssistant,
    entry: ZontConfigEntry,
    controller_device_id: str,
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
            via_device_id=controller_device_id,
        )


@callback
def _async_cleanup_excluded_object_devices(
    hass: HomeAssistant,
    entry: ZontConfigEntry,
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


async def async_unload_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Unload a ZONT config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    runtime_data = entry.runtime_data
    try:
        await runtime_data.object_entities.async_shutdown()
    finally:
        try:
            if runtime_data.export_manager is not None:
                await runtime_data.export_manager.async_shutdown()
        finally:
            try:
                await runtime_data.coordinator.async_shutdown()
            finally:
                await runtime_data.client.async_stop()
    return True


async def _async_entry_updated(
    hass: HomeAssistant,
    entry: ZontConfigEntry,
) -> None:
    """Apply options live and reload only changed connection settings."""
    runtime_data = entry.runtime_data
    async with runtime_data.options_lock:
        connection_settings = _connection_settings(entry)
        if connection_settings != runtime_data.connection_settings:
            await hass.config_entries.async_reload(entry.entry_id)
            return

        options = dict(entry.options)
        if options == runtime_data.options:
            return

        if runtime_data.export_manager is not None:
            await runtime_data.export_manager.async_reconfigure(options)
        runtime_data.coordinator.async_apply_options()
        await runtime_data.object_entities.async_reconcile()

        controller_device_id = runtime_data.controller_device_id
        if controller_device_id is not None:
            _async_sync_object_devices(
                hass,
                entry,
                controller_device_id,
            )
        _async_cleanup_excluded_object_devices(
            hass,
            entry,
            entry.unique_id or entry.entry_id,
        )
        runtime_data.options = options


def _connection_settings(entry: ZontConfigEntry) -> tuple[str, str, str]:
    """Return the settings whose change requires a new WebSocket client."""
    return (
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )


@callback
def _async_apply_controller_info(
    hass: HomeAssistant,
    entry: ZontConfigEntry,
    device_id: str,
    info: ZontControllerInfo,
) -> None:
    """Persist refreshed controller data and update its device registry entry."""
    title = controller_entry_title(info, entry.data[CONF_HOST])
    previous_auto_title = entry.data.get(CONF_AUTO_TITLE)
    title_is_managed = (
        entry.title == previous_auto_title or entry.title == "ZONT WebSocket"
    )
    data = dict(entry.data)
    data[CONF_CONTROLLER] = info.as_dict()
    data[CONF_AUTO_TITLE] = title
    updated_title = title if title_is_managed else entry.title
    if data != entry.data or updated_title != entry.title:
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            title=updated_title,
        )

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
