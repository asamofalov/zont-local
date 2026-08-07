"""Diagnostics for the ZONT WebSocket integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from .client import ZontWsClient


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontWsClient],
) -> dict[str, Any]:
    """Return non-sensitive diagnostics for a config entry."""
    client = entry.runtime_data
    return {
        "config": {"url": entry.data[CONF_URL]},
        "connection": {
            "connected": client.is_connected,
            "last_error": client.last_error,
            "reconnect_count": client.reconnect_count,
            "pending_commands": client.pending_count,
        },
    }
