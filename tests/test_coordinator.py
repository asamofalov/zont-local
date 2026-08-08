"""Tests for the shared ZONT data coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.controller import (
    COMMAND_SERVER_INFO,
    COMMAND_SUPPLY_VOLTAGE,
    ZontCommunicationChannel,
)
from custom_components.zont_ws.coordinator import ZontDataUpdateCoordinator
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _coordinator(
    hass: HomeAssistant,
) -> tuple[ZontDataUpdateCoordinator, MagicMock]:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="ABCDEF123456", data={})
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.async_send_system_command = AsyncMock()
    coordinator = ZontDataUpdateCoordinator(
        hass,
        entry,
        client,
        initial_info=None,
        on_controller_info=MagicMock(),
    )
    return coordinator, client


async def test_refresh_builds_one_controller_snapshot(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 1 1 0",
        "#S6:123 0",
    ]

    await coordinator.async_refresh()

    status = coordinator.data.controller
    assert coordinator.last_update_success
    assert status.server_status is not None
    assert status.server_status.cloud_connected
    assert status.server_status.channels == {
        ZontCommunicationChannel.GSM,
        ZontCommunicationChannel.WIFI,
    }
    assert status.server_status.channel_state == "gsm_wifi"
    assert status.supply_voltage == 12.3
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=3.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=3.0),
    ]


async def test_invalid_source_is_disabled_without_breaking_others(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:!",
        "#S6:123 0",
        "#S6:124 0",
    ]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.controller.server_status is None
    assert coordinator.data.controller.supply_voltage == 12.4
    assert coordinator.disabled_sources == ("server_status",)
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=3.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=3.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=3.0),
    ]


async def test_disconnected_client_marks_snapshot_unavailable(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.is_connected = False

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    client.async_send_system_command.assert_not_awaited()


async def test_unchanged_snapshot_does_not_notify_listeners(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    listener = MagicMock()
    unsubscribe = coordinator.async_add_listener(listener)

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    listener.assert_called_once()
    unsubscribe()
    await coordinator.async_shutdown()
