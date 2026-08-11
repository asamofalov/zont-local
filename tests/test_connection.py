"""Tests for the Home Assistant adapter around the pure ZONT client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from custom_components.zont_local.connection import ZontConnectionManager
from custom_components.zont_local.const import DOMAIN, EVENT_MESSAGE, connection_signal
from custom_components.zont_local.protocol import ZontAuthenticationError
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)


class FakeProtocolClient:
    """Minimal observable protocol client for manager lifecycle tests."""

    def __init__(self, error: Exception | None = None) -> None:
        self.is_connected = False
        self.error = error
        self.stopped = asyncio.Event()
        self.message_listener: Callable[[Any], None] | None = None
        self.connection_listener: Callable[[bool], None] | None = None
        self.removed = 0

    async def async_connect(self) -> None:
        self.is_connected = True

    async def async_supervise(self) -> None:
        if self.error is not None:
            raise self.error
        await self.stopped.wait()

    async def async_stop(self) -> None:
        self.is_connected = False
        self.stopped.set()

    def async_add_message_listener(
        self, listener: Callable[[Any], None]
    ) -> Callable[[], None]:
        self.message_listener = listener
        return self._remove_listener

    def async_add_connection_listener(
        self, listener: Callable[[bool], None]
    ) -> Callable[[], None]:
        self.connection_listener = listener
        return self._remove_listener

    def _remove_listener(self) -> None:
        self.removed += 1


async def test_manager_bridges_events_and_connection_dispatcher(
    hass: HomeAssistant,
) -> None:
    assert EVENT_MESSAGE == "zont_local_event"

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    client = FakeProtocolClient()
    manager = ZontConnectionManager(hass, entry, client, "device")  # type: ignore[arg-type]
    events = async_capture_events(hass, EVENT_MESSAGE)
    connection_states: list[bool] = []
    remove_dispatcher = async_dispatcher_connect(
        hass,
        connection_signal(entry.entry_id),
        connection_states.append,
    )

    await manager.async_start()
    assert client.message_listener is not None
    assert client.connection_listener is not None
    client.message_listener({"id": 7, "type": 14, "s": 1})
    client.connection_listener(False)
    await hass.async_block_till_done()

    assert connection_states == [True, False]
    assert events[-1].data == {
        "device_id": "device",
        "payload": {"id": 7, "type": 14, "s": 1},
    }

    remove_dispatcher()
    await manager.async_shutdown()
    assert client.removed == 2


@pytest.mark.parametrize("state", [CoreState.stopping, CoreState.final_write])
async def test_manager_ignores_disconnect_during_home_assistant_shutdown(
    hass: HomeAssistant,
    state: CoreState,
) -> None:
    """A planned HA shutdown must not make entities temporarily unavailable."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    client = FakeProtocolClient()
    manager = ZontConnectionManager(hass, entry, client, "device")  # type: ignore[arg-type]
    connection_states: list[bool] = []
    remove_dispatcher = async_dispatcher_connect(
        hass,
        connection_signal(entry.entry_id),
        connection_states.append,
    )

    await manager.async_start()
    assert client.connection_listener is not None
    hass.set_state(state)
    client.connection_listener(False)
    await hass.async_block_till_done()

    assert connection_states == [True]

    remove_dispatcher()
    await manager.async_shutdown()


async def test_manager_starts_reauth_for_terminal_authentication_failure(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.async_start_reauth = MagicMock()  # type: ignore[method-assign]
    client = FakeProtocolClient(ZontAuthenticationError())
    manager = ZontConnectionManager(hass, entry, client, "device")  # type: ignore[arg-type]

    await manager.async_start()
    await hass.async_block_till_done()

    entry.async_start_reauth.assert_called_once_with(hass)
    await manager.async_shutdown()
