"""Diagnostics for the ZONT Local integration."""

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
            "unsupported_sources": coordinator.unsupported_sources,
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
            "power_source": (
                status.power_source.value if status.power_source is not None else None
            ),
            "wifi_connected": (
                status.wifi_status.connected if status.wifi_status is not None else None
            ),
            "wifi_signal_percent": (
                status.wifi_status.signal_percent
                if status.wifi_status is not None
                else None
            ),
            "ethernet_connected": (
                status.ethernet_status.connected
                if status.ethernet_status is not None
                else None
            ),
            "gsm_registration": (
                status.gsm_status.registration.value
                if status.gsm_status is not None
                else None
            ),
            "gsm_signal_percent": (
                status.gsm_status.signal_percent
                if status.gsm_status is not None
                else None
            ),
        },
    }
