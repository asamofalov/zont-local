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
    ZontProtocolError,
    ZontWsClient,
)
from custom_components.zont_ws.const import (
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    DOMAIN,
    connection_signal,
)
from custom_components.zont_ws.controller import ZontControllerInfo
from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
}
SERIAL_NUMBER = "ABCDEF123456"
CONTROLLER_INFO = ZontControllerInfo(
    serial_number=SERIAL_NUMBER,
    model="H1V02 PRO",
    board_model="700",
    firmware_version="625",
)


async def test_legacy_entry_requires_adding_controller_again(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_URL: "ws://controller.local/ws",
            CONF_USERNAME: "legacy-user",
            CONF_PASSWORD: "legacy-password",
        },
    )
    entry.add_to_hass(hass)

    assert not await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data == {
        CONF_URL: "ws://controller.local/ws",
        CONF_USERNAME: "legacy-user",
        CONF_PASSWORD: "legacy-password",
    }


async def test_setup_stores_runtime_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
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
    assert entry.runtime_data._url == "ws://192.0.2.10/ws"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL_NUMBER)})
    assert device is not None
    assert device.name == "ZONT H1V02 PRO"
    assert device.manufacturer == "ZONT"
    assert device.model == "H1V02 PRO"
    assert device.model_id == "700"
    assert device.sw_version == "625"
    assert device.serial_number == SERIAL_NUMBER
    assert device.configuration_url == "http://192.0.2.10"


async def test_setup_refreshes_controller_information(hass: HomeAssistant) -> None:
    old_info = ZontControllerInfo(
        serial_number=SERIAL_NUMBER,
        model="H1V02 PRO",
        board_model="700",
        firmware_version="624",
    )
    old_title = "ZONT H1V02 PRO (192.0.2.10)"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=old_title,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: old_info.as_dict(),
            CONF_AUTO_TITLE: old_title,
        },
    )
    entry.add_to_hass(hass)

    async def async_start(client: ZontWsClient) -> None:
        client._is_connected = True

    with (
        patch.object(ZontWsClient, "async_start", new=async_start),
        patch(
            "custom_components.zont_ws.async_refresh_controller_info",
            new=AsyncMock(return_value=CONTROLLER_INFO),
        ) as refresh,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)
        await hass.async_block_till_done()

    refresh.assert_awaited_once_with(entry.runtime_data, SERIAL_NUMBER)
    assert entry.data[CONF_CONTROLLER] == CONTROLLER_INFO.as_dict()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL_NUMBER)})
    assert device is not None
    assert device.sw_version == "625"


async def test_failed_information_refresh_is_disabled_until_restart(
    hass: HomeAssistant,
) -> None:
    title = "ZONT H1V02 PRO (192.0.2.10)"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: title,
        },
    )
    entry.add_to_hass(hass)

    async def async_start(client: ZontWsClient) -> None:
        client._is_connected = True

    with (
        patch.object(ZontWsClient, "async_start", new=async_start),
        patch(
            "custom_components.zont_ws.async_refresh_controller_info",
            new=AsyncMock(side_effect=ZontProtocolError),
        ) as refresh,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)
        await hass.async_block_till_done()
        async_dispatcher_send(hass, connection_signal(entry.entry_id), True)
        await hass.async_block_till_done()

    refresh.assert_awaited_once_with(entry.runtime_data, SERIAL_NUMBER)


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
