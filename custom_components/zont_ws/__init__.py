"""ZONT WebSocket integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    ZontRequestTimeoutError,
    ZontWsClient,
)
from .const import (
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    PLATFORMS,
    connection_signal,
)
from .controller import (
    ZontControllerInfo,
    async_refresh_controller_info,
    controller_configuration_url,
    controller_device_name,
    controller_entry_title,
    controller_websocket_url,
)
from .services import async_setup_services

type ZontConfigEntry = ConfigEntry[ZontWsClient]

_LOGGER = logging.getLogger(__name__)


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
        await client.async_start()
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

    entry.runtime_data = client
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await client.async_stop()
        raise

    _async_setup_controller_info_refresh(
        hass,
        entry,
        client,
        device.id,
        controller_info,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Unload a ZONT config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.async_stop()
    return True


def _async_setup_controller_info_refresh(
    hass: HomeAssistant,
    entry: ZontConfigEntry,
    client: ZontWsClient,
    device_id: str,
    initial_info: ZontControllerInfo | None,
) -> None:
    """Refresh controller descriptions without delaying config entry setup."""
    serial_number = (
        initial_info.serial_number if initial_info is not None else entry.unique_id
    )
    if serial_number is None:
        return

    refresh_task: asyncio.Task[None] | None = None
    refresh_enabled = True

    async def async_refresh() -> None:
        nonlocal refresh_enabled, refresh_task
        try:
            info = await async_refresh_controller_info(client, serial_number)
        except asyncio.CancelledError:
            raise
        except (ZontRequestTimeoutError, ZontProtocolError):
            refresh_enabled = False
            _LOGGER.warning(
                "Unable to refresh ZONT controller information; "
                "further attempts are disabled until the next Home Assistant start"
            )
        except ZontConnectionError:
            pass
        else:
            _async_apply_controller_info(hass, entry, device_id, info)
        finally:
            refresh_task = None

    @callback
    def async_schedule_refresh(connected: bool) -> None:
        nonlocal refresh_task
        if not connected or not refresh_enabled:
            return
        if refresh_task is not None and not refresh_task.done():
            return
        refresh_task = entry.async_create_background_task(
            hass,
            async_refresh(),
            f"{DOMAIN} controller information refresh",
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            connection_signal(entry.entry_id),
            async_schedule_refresh,
        )
    )
    async_schedule_refresh(client.is_connected)


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
