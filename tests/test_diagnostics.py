"""Tests for ZONT diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.diagnostics import async_get_config_entry_diagnostics
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_diagnostics_exclude_credentials(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: "ws://controller.local/ws",
            CONF_USERNAME: "secret-user",
            CONF_PASSWORD: "secret-password",
        },
    )
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.last_error = None
    client.reconnect_count = 2
    client.pending_count = 1
    entry.runtime_data = client

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result == {
        "config": {"url": "ws://controller.local/ws"},
        "connection": {
            "connected": True,
            "last_error": None,
            "reconnect_count": 2,
            "pending_commands": 1,
        },
    }
    assert "secret-user" not in str(result)
    assert "secret-password" not in str(result)
