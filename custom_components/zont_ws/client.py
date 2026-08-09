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
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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

type ZontCommand = str | int | float


class ZontError(Exception):
    """Base exception for ZONT client errors."""


class ZontConnectionError(ZontError):
    """Raised when the controller cannot be reached."""


class ZontAuthenticationError(ZontError):
    """Raised when the controller rejects the credentials."""


class ZontProtocolError(ZontError):
    """Raised when the controller sends an invalid protocol message."""


class ZontRequestTimeoutError(ZontError):
    """Raised when a protocol response is not received in time."""


class ZontCommandTimeoutError(ZontRequestTimeoutError):
    """Raised when a command response is not received in time."""


@dataclass(frozen=True, slots=True)
class ZontCredentials:
    """Credentials used to authenticate with the controller."""

    username: str
    password: str


@dataclass(slots=True)
class _RequestSlot:
    """Lock and reference count for one object identifier."""

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


async def async_request_system_commands(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
    commands: tuple[str, ...],
    response_timeout: float = COMMAND_TIMEOUT,
) -> list[str]:
    """Run serialized system commands on one temporary connection."""
    ws = await _async_open_websocket(session, url, credentials)
    try:
        responses = []
        for command in commands:
            responses.append(
                await _async_request_system_command(ws, command, response_timeout)
            )
        return responses
    finally:
        await _async_close_websocket(ws)


async def _async_request_system_command(
    ws: ClientWebSocketResponse,
    command: str,
    response_timeout: float,
) -> str:
    """Send one system command on a temporary config-flow connection."""
    try:
        await ws.send_str(
            json.dumps({"scmd": command}, ensure_ascii=False, separators=(",", ":"))
        )
        async with asyncio.timeout(response_timeout):
            while True:
                message = await ws.receive()
                if message.type is WSMsgType.TEXT:
                    try:
                        response = json.loads(message.data)
                    except (TypeError, json.JSONDecodeError) as err:
                        raise ZontProtocolError(
                            "System command response is not JSON"
                        ) from err
                    if not isinstance(response, Mapping) or "scmdres" not in response:
                        continue
                    result = response["scmdres"]
                    if not isinstance(result, str):
                        raise ZontProtocolError("System command response is not text")
                    return result
                if message.type in (
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSED,
                    WSMsgType.CLOSING,
                    WSMsgType.ERROR,
                ):
                    raise ZontConnectionError("The WebSocket was closed")
                if message.type is WSMsgType.BINARY:
                    raise ZontProtocolError(
                        "Binary WebSocket messages are not supported"
                    )
    except TimeoutError as err:
        raise ZontRequestTimeoutError("No response for system command") from err
    except (ClientError, OSError, RuntimeError) as err:
        raise ZontConnectionError("Unable to request controller data") from err


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
        self._command_slots: dict[int, _RequestSlot] = {}
        self._state_slots: dict[int, _RequestSlot] = {}
        self._ids_lock = asyncio.Lock()
        self._scmd_lock = asyncio.Lock()
        self._pending_commands: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._pending_states: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._pending_ids: asyncio.Future[list[int]] | None = None
        self._pending_scmd: asyncio.Future[str] | None = None
        self._message_listeners: set[Callable[[Any], None]] = set()

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
        return len(self._pending_commands)

    async def async_start(self, entry: ConfigEntry[Any]) -> None:
        """Connect once and start the connection supervisor."""
        if self._supervisor_task is not None:
            return

        self._stop.clear()
        self._ws = await _async_open_websocket(
            self._session, self._url, self._credentials
        )
        self._set_connected(True)
        self._supervisor_task = entry.async_create_background_task(
            self._hass,
            self._async_supervisor(),
            f"{DOMAIN} WebSocket supervisor",
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
        self._state_slots.clear()
        self._message_listeners.clear()

    @callback
    def async_add_message_listener(
        self, listener: Callable[[Any], None]
    ) -> Callable[[], None]:
        """Subscribe to decoded unsolicited controller messages."""
        self._message_listeners.add(listener)

        @callback
        def async_remove_listener() -> None:
            self._message_listeners.discard(listener)

        return async_remove_listener

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
        """Route a protocol response or publish an unsolicited event."""
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            self._fire_event(raw)
            return

        if isinstance(data, Mapping):
            response = dict(data)
            if self._resolve_command_response(response):
                return
            if self._resolve_ids_response(response):
                return
            if "scmdres" in response:
                self._resolve_system_command_response(response)
                return
            if self._resolve_state_response(response):
                return

        self._fire_event(data)

    def _resolve_command_response(self, response: dict[str, Any]) -> bool:
        """Resolve a documented object command response."""
        if "cmdres" not in response:
            return False

        message_id = _message_id(response)
        if message_id is None:
            return False

        future = self._pending_commands.pop(message_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(response)
        return True

    def _resolve_ids_response(self, response: dict[str, Any]) -> bool:
        """Resolve the one serialized object ID request."""
        if "ids" not in response:
            return False

        future = self._pending_ids
        if future is None:
            return False
        self._pending_ids = None

        ids = response["ids"]
        if not isinstance(ids, list) or any(type(item) is not int for item in ids):
            if not future.done():
                future.set_exception(ZontProtocolError("Object IDs are not integers"))
            return True

        if not future.done():
            future.set_result(ids)
        return True

    def _resolve_system_command_response(self, response: dict[str, Any]) -> None:
        """Resolve a system command without exposing unmatched sensitive data."""
        future = self._pending_scmd
        if future is None:
            return
        self._pending_scmd = None

        result = response["scmdres"]
        if not isinstance(result, str):
            if not future.done():
                future.set_exception(
                    ZontProtocolError("System command response is not text")
                )
            return

        if not future.done():
            future.set_result(result)

    def _resolve_state_response(self, response: dict[str, Any]) -> bool:
        """Resolve an object state response by its identifier."""
        if "type" not in response and "failed" not in response:
            return False

        message_id = _message_id(response)
        if message_id is None:
            return False

        future = self._pending_states.pop(message_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(response)
        return True

    def _fire_event(self, payload: Any) -> None:
        """Publish an unsolicited controller message on the HA event bus."""
        for listener in tuple(self._message_listeners):
            try:
                listener(payload)
            except Exception:  # pragma: no cover - defensive listener isolation
                _LOGGER.exception("Error in ZONT message listener")
        self._hass.bus.async_fire(
            EVENT_MESSAGE,
            {"device_id": self._device_id, "payload": payload},
        )

    async def async_send_command(
        self,
        command_id: int,
        command: ZontCommand,
        response_timeout: float = COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a command and wait for the matching response."""
        async with self._async_request_slot(self._command_slots, command_id):
            ws = self._connected_websocket()

            future: asyncio.Future[dict[str, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending_commands[command_id] = future
            try:
                try:
                    await self._async_send_payload(
                        ws, {"id": command_id, "cmd": command}
                    )
                    async with asyncio.timeout(response_timeout):
                        return await future
                except asyncio.CancelledError:
                    await self._async_invalidate_connection(ws)
                    raise
                except TimeoutError as err:
                    await self._async_invalidate_connection(ws)
                    raise ZontCommandTimeoutError(
                        f"No response for command {command_id}"
                    ) from err
            finally:
                if self._pending_commands.get(command_id) is future:
                    self._pending_commands.pop(command_id, None)
                if not future.done():
                    future.cancel()

    async def async_get_object_ids(
        self,
        object_type: int,
        response_timeout: float = COMMAND_TIMEOUT,
    ) -> list[int]:
        """Request object identifiers of one type."""
        if type(object_type) is not int or not 0 <= object_type <= 255:
            raise ValueError("Object type must be an integer from 0 to 255")

        async with self._ids_lock:
            ws = self._connected_websocket()
            future: asyncio.Future[list[int]] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending_ids = future
            try:
                try:
                    await self._async_send_payload(ws, {"req_ids": object_type})
                    async with asyncio.timeout(response_timeout):
                        return await future
                except asyncio.CancelledError:
                    await self._async_invalidate_connection(ws)
                    raise
                except TimeoutError as err:
                    await self._async_invalidate_connection(ws)
                    raise ZontRequestTimeoutError(
                        f"No object ID response for type {object_type}"
                    ) from err
            finally:
                if self._pending_ids is future:
                    self._pending_ids = None
                if not future.done():
                    future.cancel()

    async def async_get_object_state(
        self,
        object_id: int,
        response_timeout: float = COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        """Request the current state of one object."""
        if type(object_id) is not int or object_id < 0:
            raise ValueError("Object ID must be a non-negative integer")

        async with self._async_request_slot(self._state_slots, object_id):
            ws = self._connected_websocket()
            future: asyncio.Future[dict[str, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending_states[object_id] = future
            try:
                try:
                    await self._async_send_payload(
                        ws, {"id": object_id, "req_state": 0}
                    )
                    async with asyncio.timeout(response_timeout):
                        return await future
                except asyncio.CancelledError:
                    await self._async_invalidate_connection(ws)
                    raise
                except TimeoutError as err:
                    await self._async_invalidate_connection(ws)
                    raise ZontRequestTimeoutError(
                        f"No state response for object {object_id}"
                    ) from err
            finally:
                if self._pending_states.get(object_id) is future:
                    self._pending_states.pop(object_id, None)
                if not future.done():
                    future.cancel()

    async def async_send_system_command(
        self,
        command: str,
        response_timeout: float = COMMAND_TIMEOUT,
    ) -> str:
        """Send one serialized system command and return its text response."""
        if not isinstance(command, str) or not command.strip():
            raise ValueError("System command must be non-empty text")

        async with self._scmd_lock:
            ws = self._connected_websocket()
            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self._pending_scmd = future
            try:
                try:
                    await self._async_send_payload(ws, {"scmd": command})
                    async with asyncio.timeout(response_timeout):
                        return await future
                except asyncio.CancelledError:
                    await self._async_invalidate_connection(ws)
                    raise
                except TimeoutError as err:
                    await self._async_invalidate_connection(ws)
                    raise ZontRequestTimeoutError(
                        "No response for system command"
                    ) from err
            finally:
                if self._pending_scmd is future:
                    self._pending_scmd = None
                if not future.done():
                    future.cancel()

    async def async_send_system_command_without_response(self, command: str) -> None:
        """Send a system command without waiting for its uncorrelated response."""
        if not isinstance(command, str) or not command.strip():
            raise ValueError("System command must be non-empty text")

        async with self._scmd_lock:
            ws = self._connected_websocket()
            try:
                await self._async_send_payload(ws, {"scmd": command})
            except asyncio.CancelledError:
                await self._async_invalidate_connection(ws)
                raise

    def _connected_websocket(self) -> ClientWebSocketResponse:
        """Return the active WebSocket or raise a connection error."""
        ws = self._ws
        if not self._is_connected or ws is None or ws.closed:
            raise ZontConnectionError("The ZONT controller is offline")
        return ws

    async def _async_send_payload(
        self,
        ws: ClientWebSocketResponse,
        payload: Mapping[str, Any],
    ) -> None:
        """Serialize and send one payload through the expected connection."""
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
            raise ZontConnectionError("Unable to send the request") from err

    async def _async_invalidate_connection(self, ws: ClientWebSocketResponse) -> None:
        """Close a broken connection and wake the supervisor."""
        if self._ws is ws:
            self._set_connected(False)
            self._fail_pending(ZontConnectionError("The ZONT connection was lost"))
        await _async_close_websocket(ws)

    @asynccontextmanager
    async def _async_request_slot(
        self,
        slots: dict[int, _RequestSlot],
        object_id: int,
    ) -> AsyncIterator[None]:
        """Serialize requests of one class that share an object identifier."""
        slot = slots.get(object_id)
        if slot is None:
            slot = slots[object_id] = _RequestSlot(asyncio.Lock())
        slot.users += 1
        try:
            async with slot.lock:
                yield
        finally:
            slot.users -= 1
            if slot.users == 0 and slots.get(object_id) is slot:
                slots.pop(object_id, None)

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
        """Fail and remove every in-flight protocol request."""
        pending: list[asyncio.Future[Any]] = [
            *self._pending_commands.values(),
            *self._pending_states.values(),
        ]
        self._pending_commands.clear()
        self._pending_states.clear()
        if self._pending_ids is not None:
            pending.append(self._pending_ids)
            self._pending_ids = None
        if self._pending_scmd is not None:
            pending.append(self._pending_scmd)
            self._pending_scmd = None
        for future in pending:
            if not future.done():
                future.set_exception(error)
                # Mark the exception retrieved. Awaiting the future still raises it,
                # while a simultaneously failing sender does not leak a warning.
                future.exception()


def _message_id(response: Mapping[str, Any]) -> int | None:
    """Return a valid lower- or upper-case protocol object identifier."""
    lower_id = response.get("id")
    upper_id = response.get("Id")

    if lower_id is not None and type(lower_id) is not int:
        return None
    if upper_id is not None and type(upper_id) is not int:
        return None
    if lower_id is not None and upper_id is not None and lower_id != upper_id:
        return None
    if type(lower_id) is int:
        return lower_id
    if type(upper_id) is int:
        return upper_id
    return None
