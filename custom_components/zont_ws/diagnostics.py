"""Diagnostics for the ZONT WebSocket integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .client import ZontWsClient
from .const import CONF_CONTROLLER
from .controller import ZontControllerInfo


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontWsClient],
) -> dict[str, Any]:
    """Return non-sensitive diagnostics for a config entry."""
    client = entry.runtime_data
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
    }
