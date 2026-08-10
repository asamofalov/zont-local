"""Home Assistant lifecycle adapter for the ZONT protocol client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, EVENT_MESSAGE, connection_signal
from .protocol import ZontAuthenticationError, ZontClient

_LOGGER = logging.getLogger(__name__)


class ZontConnectionManager:
    """Bind a pure ZONT client to one Home Assistant config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry[Any],
        client: ZontClient,
        device_id: str,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self._device_id = device_id
        self._supervisor_task: asyncio.Task[None] | None = None
        self._remove_listeners: list[Callable[[], None]] = []
        self._reauth_requested = False

    async def async_start(self) -> None:
        """Connect once, expose protocol events, and start supervision."""
        if self._supervisor_task is not None:
            return
        await self._client.async_connect()
        self._remove_listeners = [
            self._client.async_add_message_listener(self._async_handle_message),
            self._client.async_add_connection_listener(
                self._async_handle_connection_state
            ),
        ]
        self._async_handle_connection_state(self._client.is_connected)
        self._supervisor_task = self._entry.async_create_background_task(
            self._hass,
            self._async_supervise(),
            f"{DOMAIN} WebSocket supervisor",
        )

    async def async_shutdown(self) -> None:
        """Stop supervision, close the socket, and remove HA callbacks."""
        await self._client.async_stop()
        task = self._supervisor_task
        self._supervisor_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        for remove_listener in self._remove_listeners:
            remove_listener()
        self._remove_listeners.clear()

    async def _async_supervise(self) -> None:
        """Translate terminal authentication failures into HA reauthentication."""
        try:
            await self._client.async_supervise()
        except asyncio.CancelledError:
            raise
        except ZontAuthenticationError:
            if not self._reauth_requested:
                self._reauth_requested = True
                self._entry.async_start_reauth(self._hass)
        except Exception:  # pragma: no cover - protocol supervisor is self-healing
            _LOGGER.exception("Unexpected ZONT supervisor failure")

    @callback
    def _async_handle_message(self, payload: Any) -> None:
        """Publish an unsolicited protocol message on the public HA event bus."""
        self._hass.bus.async_fire(
            EVENT_MESSAGE,
            {"device_id": self._device_id, "payload": payload},
        )

    @callback
    def _async_handle_connection_state(self, connected: bool) -> None:
        """Notify entities about authenticated connection-state changes."""
        async_dispatcher_send(
            self._hass,
            connection_signal(self._entry.entry_id),
            connected,
        )
