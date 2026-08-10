"""Diagnostics for the ZONT WebSocket integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_CONTROLLER
from .protocol.controller import ZontControllerInfo
from .runtime import ZontRuntimeData


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
) -> dict[str, Any]:
    """Return non-sensitive diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    client = runtime_data.client
    coordinator = runtime_data.coordinator
    export_manager = runtime_data.export_manager
    status = coordinator.data.controller
    info = ZontControllerInfo.from_mapping(entry.data.get(CONF_CONTROLLER))
    return {
        "config": {"host": entry.data[CONF_HOST]},
        "controller": (
            {
                "model": info.model,
                "board_model": info.board_model,
                "firmware_version": info.firmware_version,
            }
            if info is not None
            else None
        ),
        "connection": {
            "connected": client.is_connected,
            "last_error": client.last_error,
            "reconnect_count": client.reconnect_count,
            "pending_commands": client.pending_count,
        },
        "exports": {
            "configured": (
                export_manager.configured_count if export_manager is not None else 0
            ),
            "active": export_manager.active_count if export_manager is not None else 0,
            "errors": export_manager.error_count if export_manager is not None else 0,
        },
        "data": {
            "last_update_success": coordinator.last_update_success,
            "disabled_sources": coordinator.disabled_sources,
            "cloud_connected": (
                status.server_status.cloud_connected
                if status.server_status is not None
                else None
            ),
            "connection_channels": (
                sorted(channel.value for channel in status.server_status.channels)
                if status.server_status is not None
                else None
            ),
            "supply_voltage": status.supply_voltage,
        },
    }
