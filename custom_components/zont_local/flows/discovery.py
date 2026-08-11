"""Controller discovery shared by ZONT options-flow sections."""

from __future__ import annotations

import logging
from typing import cast

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..data import ZontData
from ..protocol import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    async_open_temporary_request_session,
)
from ..protocol.controller import controller_websocket_url
from ..protocol.discovery import async_discover_objects_from_requests
from ..protocol.heating_config import ZontHeatingModeConfiguration
from ..protocol.heating_modes import (
    async_discover_heating_modes,
    eligible_off_modes,
    relevant_heating_circuit_ids,
)
from ..protocol.objects import ZontObject
from ..runtime import ZontRuntimeData
from .schemas import (
    ERROR_CANNOT_CONNECT,
    ERROR_CANNOT_READ_DEVICES,
    ERROR_CANNOT_READ_MODES,
    ERROR_INVALID_AUTH,
    ERROR_UNKNOWN,
)

_LOGGER = logging.getLogger(__name__)


@callback
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
            objects = await async_discover_objects_from_requests(requests)
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
