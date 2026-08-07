"""Tests for ZONT integration setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.zont_ws import (
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.zont_ws.client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontWsClient,
)
from custom_components.zont_ws.const import DOMAIN
from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTRY_DATA = {
    CONF_URL: "ws://controller.local/ws",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
}


async def test_migrate_legacy_entry_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONF_URL: "ws://controller.local/ws",
            "user": "legacy-user",
            "pass": "legacy-password",
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data == {
        CONF_URL: "ws://controller.local/ws",
        CONF_USERNAME: "legacy-user",
        CONF_PASSWORD: "legacy-password",
    }


async def test_setup_stores_runtime_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)

    assert isinstance(entry.runtime_data, ZontWsClient)


@pytest.mark.parametrize(
    ("client_error", "expected_error"),
    [
        (ZontConnectionError(), ConfigEntryNotReady),
        (ZontAuthenticationError(), ConfigEntryAuthFailed),
    ],
)
async def test_setup_maps_client_errors(
    hass: HomeAssistant,
    client_error: Exception,
    expected_error: type[Exception],
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)

    with (
        patch.object(
            ZontWsClient,
            "async_start",
            new=AsyncMock(side_effect=client_error),
        ),
        pytest.raises(expected_error),
    ):
        await async_setup_entry(hass, entry)


async def test_unload_stops_client(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    client = MagicMock(spec=ZontWsClient)
    client.async_stop = AsyncMock()
    entry.runtime_data = client

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, entry)

    client.async_stop.assert_awaited_once()
