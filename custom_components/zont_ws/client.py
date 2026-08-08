"""Asynchronous ZONT WebSocket client."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    COMMAND_TIMEOUT,
    CONNECTION_TIMEOUT,
    DOMAIN,
    EVENT_MESSAGE,
    RECONNECT_DELAYS,
    WS_HEARTBEAT,
    connection_signal,
)

_LOGGER = logging.getLogger(__name__)


class ZontError(Exception):
    """Base exception for ZONT client errors."""


class ZontConnectionError(ZontError):
    """Raised when the controller cannot be reached."""


class ZontAuthenticationError(ZontError):
    """Raised when the controller rejects the credentials."""


class ZontProtocolError(ZontError):
    """Raised when the controller sends an invalid protocol message."""


class ZontCommandTimeoutError(ZontError):
    """Raised when a command response is not received in time."""


@dataclass(frozen=True, slots=True)
class ZontCredentials:
    """Credentials used to authenticate with the controller."""

    username: str
    password: str


@dataclass(slots=True)
class _CommandSlot:
    """Lock and reference count for one command identifier."""

    lock: asyncio.Lock
    users: int = 0


async def _async_close_websocket(ws: ClientWebSocketResponse | None) -> None:
    """Close a WebSocket without hiding cancellation of the caller."""
    if ws is None or ws.closed:
        return

    close_task = asyncio.create_task(ws.close())
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await close_task
        raise
    except Exception:  # pragma: no cover - aiohttp close is deliberately best effort
        _LOGGER.debug("Error while closing ZONT WebSocket", exc_info=True)


async def _async_open_websocket(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
) -> ClientWebSocketResponse:
    """Open and authenticate a WebSocket connection."""
    ws: ClientWebSocketResponse | None = None
    try:
        async with asyncio.timeout(CONNECTION_TIMEOUT):
            ws = await session.ws_connect(url, heartbeat=WS_HEARTBEAT)
            auth_payload = {
                "user": credentials.username,
                "pass": credentials.password,
            }
            await ws.send_str(
                json.dumps(auth_payload, ensure_ascii=False, separators=(",", ":"))
            )

            message = await ws.receive()
            if message.type is not WSMsgType.TEXT:
                raise ZontProtocolError("Authentication response is not text")

            try:
                response = json.loads(message.data)
            except (TypeError, json.JSONDecodeError) as err:
                raise ZontProtocolError("Authentication response is not JSON") from err

            if not isinstance(response, Mapping):
                raise ZontProtocolError("Authentication response is not an object")
            if response.get("auth") != 200:
                raise ZontAuthenticationError("Authentication was rejected")

            return ws
    except asyncio.CancelledError:
        await _async_close_websocket(ws)
        raise
    except (ZontAuthenticationError, ZontProtocolError):
        await _async_close_websocket(ws)
        raise
    except (TimeoutError, ClientError, OSError) as err:
        await _async_close_websocket(ws)
        raise ZontConnectionError("Unable to connect to the controller") from err
    except Exception as err:
        await _async_close_websocket(ws)
        raise ZontConnectionError("Unable to open the WebSocket") from err


async def async_validate_connection(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
) -> None:
    """Validate a URL and credentials without starting background work."""
    ws = await _async_open_websocket(session, url, credentials)
    await _async_close_websocket(ws)


class ZontWsClient:
    """Maintain a single authenticated WebSocket connection to ZONT."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: ClientSession,
        url: str,
        credentials: ZontCredentials,
        entry_id: str,
        device_id: str,
        on_authentication_error: Callable[[], None],
    ) -> None:
        """Initialize the client."""
        self._hass = hass
        self._session = session
        self._url = url
        self._credentials = credentials
        self._entry_id = entry_id
        self._device_id = device_id
        self._on_authentication_error = on_authentication_error

        self._ws: ClientWebSocketResponse | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._command_slots: dict[int, _CommandSlot] = {}
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

        self._is_connected = False
        self._last_error: str | None = None
        self._reconnect_count = 0
        self._outage_logged = False
        self._reauth_requested = False

    @property
    def is_connected(self) -> bool:
        """Return whether the client has an authenticated connection."""
        return self._is_connected

    @property
    def last_error(self) -> str | None:
        """Return the last error category."""
        return self._last_error

    @property
    def reconnect_count(self) -> int:
        """Return the number of successful reconnects."""
        return self._reconnect_count

    @property
    def pending_count(self) -> int:
        """Return the number of commands waiting for a response."""
        return len(self._pending)

    async def async_start(self) -> None:
        """Connect once and start the connection supervisor."""
        if self._supervisor_task is not None:
            return

        self._stop.clear()
        self._ws = await _async_open_websocket(
            self._session, self._url, self._credentials
        )
        self._set_connected(True)
        self._supervisor_task = self._hass.async_create_task(
            self._async_supervisor(), f"{DOMAIN} WebSocket supervisor"
        )

    async def async_stop(self) -> None:
        """Stop all background work and close the connection."""
        self._stop.set()
        self._set_connected(False)
        self._fail_pending(ZontConnectionError("The ZONT client has stopped"))

        ws = self._ws
        self._ws = None
        await _async_close_websocket(ws)

        task = self._supervisor_task
        self._supervisor_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        self._command_slots.clear()

    async def _async_supervisor(self) -> None:
        """Read messages and own every reconnect attempt."""
        reconnect_index = 0
        try:
            while not self._stop.is_set():
                ws = self._ws
                if ws is None:
                    try:
                        ws = await _async_open_websocket(
                            self._session, self._url, self._credentials
                        )
                    except asyncio.CancelledError:
                        raise
                    except ZontAuthenticationError:
                        self._record_error("authentication")
                        self._request_reauthentication()
                        return
                    except ZontProtocolError:
                        self._record_error("protocol")
                        await self._async_wait_before_reconnect(reconnect_index)
                        reconnect_index = min(
                            reconnect_index + 1, len(RECONNECT_DELAYS) - 1
                        )
                        continue
                    except ZontConnectionError:
                        self._record_error("connection")
                        await self._async_wait_before_reconnect(reconnect_index)
                        reconnect_index = min(
                            reconnect_index + 1, len(RECONNECT_DELAYS) - 1
                        )
                        continue

                    if self._stop.is_set():
                        await _async_close_websocket(ws)
                        return

                    self._ws = ws
                    self._reconnect_count += 1
                    reconnect_index = 0
                    self._set_connected(True)
                    if self._outage_logged:
                        _LOGGER.info("ZONT WebSocket connection restored")
                        self._outage_logged = False

                try:
                    await self._async_reader_loop(ws)
                except asyncio.CancelledError:
                    raise
                except ZontProtocolError:
                    self._record_error("protocol")
                except ZontConnectionError:
                    self._record_error("connection")
                finally:
                    if self._ws is ws:
                        self._ws = None
                    self._set_connected(False)
                    self._fail_pending(
                        ZontConnectionError("The ZONT connection was lost")
                    )
                    await _async_close_websocket(ws)

                if not self._stop.is_set():
                    await self._async_wait_before_reconnect(reconnect_index)
                    reconnect_index = min(
                        reconnect_index + 1, len(RECONNECT_DELAYS) - 1
                    )
        finally:
            ws = self._ws
            self._ws = None
            self._set_connected(False)
            self._fail_pending(ZontConnectionError("The ZONT connection was closed"))
            await _async_close_websocket(ws)

    async def _async_reader_loop(self, ws: ClientWebSocketResponse) -> None:
        """Read and classify messages from one WebSocket."""
        while not self._stop.is_set():
            try:
                message = await ws.receive()
            except asyncio.CancelledError:
                raise
            except (ClientError, OSError) as err:
                raise ZontConnectionError("Unable to receive WebSocket data") from err

            if message.type is WSMsgType.TEXT:
                self._handle_incoming(message.data)
                continue
            if message.type in (
                WSMsgType.CLOSE,
                WSMsgType.CLOSED,
                WSMsgType.CLOSING,
                WSMsgType.ERROR,
            ):
                raise ZontConnectionError("The WebSocket was closed")
            if message.type is WSMsgType.BINARY:
                raise ZontProtocolError("Binary WebSocket messages are not supported")

    def _handle_incoming(self, raw: str) -> None:
        """Resolve a command response or publish an unsolicited event."""
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            self._fire_event(raw)
            return

        if isinstance(data, Mapping):
            response = dict(data)
            message_id = response.get("id")
            if (
                type(message_id) is int
                and (future := self._pending.pop(message_id, None)) is not None
            ):
                if not future.done():
                    future.set_result(response)
                return

        self._fire_event(data)

    def _fire_event(self, payload: Any) -> None:
        """Publish an unsolicited controller message on the HA event bus."""
        self._hass.bus.async_fire(
            EVENT_MESSAGE,
            {"device_id": self._device_id, "payload": payload},
        )

    async def async_send_command(
        self,
        command_id: int,
        command: str,
        response_timeout: float = COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a command and wait for the matching response."""
        async with self._async_command_slot(command_id):
            ws = self._ws
            if not self._is_connected or ws is None or ws.closed:
                raise ZontConnectionError("The ZONT controller is offline")

            future: asyncio.Future[dict[str, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending[command_id] = future
            try:
                payload = {"id": command_id, "cmd": command}
                try:
                    async with self._send_lock:
                        if self._ws is not ws or ws.closed:
                            raise ZontConnectionError(
                                "The ZONT connection changed before sending"
                            )
                        await ws.send_str(
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        )
                except asyncio.CancelledError:
                    raise
                except ZontConnectionError:
                    raise
                except (ClientError, OSError) as err:
                    await self._async_invalidate_connection(ws)
                    raise ZontConnectionError("Unable to send the command") from err

                try:
                    async with asyncio.timeout(response_timeout):
                        return await future
                except TimeoutError as err:
                    raise ZontCommandTimeoutError(
                        f"No response for command {command_id}"
                    ) from err
            finally:
                if self._pending.get(command_id) is future:
                    self._pending.pop(command_id, None)
                if not future.done():
                    future.cancel()

    async def _async_invalidate_connection(self, ws: ClientWebSocketResponse) -> None:
        """Close a broken connection and wake the supervisor."""
        if self._ws is ws:
            self._set_connected(False)
            self._fail_pending(ZontConnectionError("The ZONT connection was lost"))
        await _async_close_websocket(ws)

    @asynccontextmanager
    async def _async_command_slot(self, command_id: int) -> AsyncIterator[None]:
        """Serialize requests that share the same protocol identifier."""
        slot = self._command_slots.get(command_id)
        if slot is None:
            slot = self._command_slots[command_id] = _CommandSlot(asyncio.Lock())
        slot.users += 1
        try:
            async with slot.lock:
                yield
        finally:
            slot.users -= 1
            if slot.users == 0 and self._command_slots.get(command_id) is slot:
                self._command_slots.pop(command_id, None)

    async def _async_wait_before_reconnect(self, index: int) -> None:
        """Wait for the next reconnect without delaying shutdown."""
        delay = RECONNECT_DELAYS[index]
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=delay)

    def _record_error(self, category: str) -> None:
        """Record an outage without repeatedly filling the log."""
        self._last_error = category
        if not self._outage_logged:
            _LOGGER.warning(
                "ZONT WebSocket connection lost; reconnecting in the background"
            )
            self._outage_logged = True

    def _request_reauthentication(self) -> None:
        """Start one Home Assistant reauthentication flow."""
        if self._reauth_requested:
            return
        self._reauth_requested = True
        self._on_authentication_error()

    def _set_connected(self, connected: bool) -> None:
        """Update connection state and notify entities."""
        if self._is_connected == connected:
            return
        self._is_connected = connected
        if connected:
            self._last_error = None
        async_dispatcher_send(
            self._hass,
            connection_signal(self._entry_id),
            connected,
        )

    def _fail_pending(self, error: ZontConnectionError) -> None:
        """Fail and remove every in-flight command."""
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)
                # Mark the exception retrieved. Awaiting the future still raises it,
                # while a simultaneously failing sender does not leak a warning.
                future.exception()
