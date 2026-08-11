"""ZONT Local integration."""

from __future__ import annotations

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .connection import ZontConnectionManager
from .const import CONF_CONTROLLER, CONFIG_ENTRY_VERSION, DOMAIN, PLATFORMS
from .coordinator import ZontDataUpdateCoordinator
from .device import (
    async_apply_controller_info,
    async_cleanup_excluded_object_devices,
    async_sync_object_devices,
)
from .export import ZontExportManager
from .issues import async_delete_entry_issues
from .presentation import controller_device_name
from .protocol import (
    ZontAuthenticationError,
    ZontClient,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
)
from .protocol.controller import (
    ZontControllerInfo,
    controller_configuration_url,
    controller_websocket_url,
)
from .runtime import ZontRuntimeData
from .services import async_setup_services

type ZontConfigEntry = ConfigEntry[ZontRuntimeData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


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
    async_cleanup_excluded_object_devices(
        hass,
        entry,
        controller_identifier,
    )

    client = ZontClient(
        session=async_get_clientsession(hass),
        url=controller_websocket_url(entry.data[CONF_HOST]),
        credentials=ZontCredentials(
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
        ),
    )
    connection = ZontConnectionManager(hass, entry, client, device.id)

    try:
        await connection.async_start()
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
        lambda info: async_apply_controller_info(
            hass,
            entry,
            device.id,
            info,
        ),
    )
    export_manager = ZontExportManager(hass, entry, client)
    entry.runtime_data = ZontRuntimeData(
        client=client,
        coordinator=coordinator,
        connection=connection,
        export_manager=export_manager,
        options=dict(entry.options),
        connection_settings=_connection_settings(entry),
        controller_device_id=device.id,
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: async_sync_object_devices(
                hass,
                entry,
            )
        )
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await entry.runtime_data.async_shutdown()
        raise

    export_manager.async_start()
    coordinator.async_start()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Unload a ZONT config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> None:
    """Remove Repair issues that belonged to a deleted config entry."""
    async_delete_entry_issues(hass, entry)


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
            async_sync_object_devices(
                hass,
                entry,
            )
        async_cleanup_excluded_object_devices(
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
