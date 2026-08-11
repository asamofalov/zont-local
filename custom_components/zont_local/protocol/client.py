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

from .constants import COMMAND_TIMEOUT, RECONNECT_DELAYS
from .errors import (
    ZontAuthenticationError,
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from .session import (
    async_close_websocket as _async_close_websocket,
)
from .session import (
    async_open_websocket as _async_open_websocket,
)
from .types import ZontCommand, ZontCredentials

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _RequestSlot:
    """Lock and reference count for one object identifier."""

    lock: asyncio.Lock
    users: int = 0


class ZontClient:
    """Maintain a single authenticated WebSocket connection to ZONT."""

    def __init__(
        self,
        session: ClientSession,
        url: str,
        credentials: ZontCredentials,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._url = url
        self._credentials = credentials

        self._ws: ClientWebSocketResponse | None = None
        self._stop = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._command_slots: dict[int, _RequestSlot] = {}
        self._state_slots: dict[int, _RequestSlot] = {}
        self._ids_lock = asyncio.Lock()
        self._scmd_lock = asyncio.Lock()
        self._named_command_lock = asyncio.Lock()
        self._pending_commands: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._pending_named_command: asyncio.Future[dict[str, Any]] | None = None
        self._pending_states: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._pending_ids: asyncio.Future[list[int]] | None = None
        self._pending_scmd: asyncio.Future[str] | None = None
        self._message_listeners: set[Callable[[Any], None]] = set()
        self._connection_listeners: set[Callable[[bool], None]] = set()

        self._is_connected = False
        self._last_error: str | None = None
        self._reconnect_count = 0
        self._outage_logged = False

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
        return len(self._pending_commands) + int(
            self._pending_named_command is not None
        )

    async def async_connect(self) -> None:
        """Open the initial authenticated connection."""
        if self._ws is not None:
            return

        self._stop.clear()
        self._ws = await _async_open_websocket(
            self._session, self._url, self._credentials
        )
        self._set_connected(True)

    async def async_supervise(self) -> None:
        """Read the connection and reconnect until stopped."""
        if self._ws is None:
            raise ZontConnectionError("The ZONT client is not connected")
        await self._async_supervisor()

    async def async_stop(self) -> None:
        """Stop all background work and close the connection."""
        self._stop.set()
        self._set_connected(False)
        self._fail_pending(ZontConnectionError("The ZONT client has stopped"))

        ws = self._ws
        self._ws = None
        await _async_close_websocket(ws)

        self._command_slots.clear()
        self._state_slots.clear()
        self._message_listeners.clear()
        self._connection_listeners.clear()

    def async_add_message_listener(
        self, listener: Callable[[Any], None]
    ) -> Callable[[], None]:
        """Subscribe to decoded unsolicited controller messages."""
        self._message_listeners.add(listener)

        def async_remove_listener() -> None:
            self._message_listeners.discard(listener)

        return async_remove_listener

    def async_add_connection_listener(
        self, listener: Callable[[bool], None]
    ) -> Callable[[], None]:
        """Subscribe to authenticated connection-state changes."""
        self._connection_listeners.add(listener)

        def async_remove_listener() -> None:
            self._connection_listeners.discard(listener)

        return async_remove_listener

    async def _async_supervisor(self) -> None:
        """Read messages and own every reconnect attempt."""
        reconnect_index = 0
        try:
            while not self._stop.is_set():
                ws = self._ws
                if ws is None:
                    try:
                        ws = await self._async_open_replacement_connection()
                    except asyncio.CancelledError:
                        raise
                    except ZontAuthenticationError as err:
                        self._record_error("authentication", err)
                        raise

                    if ws is None:
                        if self._stop.is_set():
                            return
                        await self._async_wait_before_reconnect(reconnect_index)
                        reconnect_index = self._next_reconnect_index(reconnect_index)
                        continue

                    reconnect_index = 0

                await self._async_run_connection(ws)

                if not self._stop.is_set():
                    await self._async_wait_before_reconnect(reconnect_index)
                    reconnect_index = self._next_reconnect_index(reconnect_index)
        finally:
            ws = self._ws
            self._ws = None
            self._set_connected(False)
            self._fail_pending(ZontConnectionError("The ZONT connection was closed"))
            await _async_close_websocket(ws)

    async def _async_open_replacement_connection(
        self,
    ) -> ClientWebSocketResponse | None:
        """Open a replacement connection or record a recoverable failure."""
        try:
            ws = await _async_open_websocket(
                self._session, self._url, self._credentials
            )
        except ZontProtocolError as err:
            self._record_error("protocol", err)
            return None
        except ZontConnectionError as err:
            self._record_error("connection", err)
            return None

        if self._stop.is_set():
            await _async_close_websocket(ws)
            return None

        self._ws = ws
        self._reconnect_count += 1
        self._set_connected(True)
        if self._outage_logged:
            _LOGGER.info("ZONT WebSocket connection restored")
            self._outage_logged = False
        return ws

    async def _async_run_connection(self, ws: ClientWebSocketResponse) -> None:
        """Read one connection until it fails, then release its resources."""
        try:
            await self._async_reader_loop(ws)
        except asyncio.CancelledError:
            raise
        except ZontProtocolError as err:
            self._record_error("protocol", err)
        except ZontConnectionError as err:
            self._record_error("connection", err)
        finally:
            if self._ws is ws:
                self._ws = None
            self._set_connected(False)
            self._fail_pending(ZontConnectionError("The ZONT connection was lost"))
            await _async_close_websocket(ws)

    @staticmethod
    def _next_reconnect_index(index: int) -> int:
        """Return the next capped reconnect delay index."""
        return min(index + 1, len(RECONNECT_DELAYS) - 1)

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
            ):
                close_code = getattr(ws, "close_code", None)
                raise ZontConnectionError(
                    f"The WebSocket was closed by the controller (code {close_code})"
                )
            if message.type is WSMsgType.ERROR:
                exception_method = getattr(ws, "exception", None)
                error = exception_method() if callable(exception_method) else None
                if error is None:
                    raise ZontConnectionError("The WebSocket reported an error")
                raise ZontConnectionError(
                    f"The WebSocket reported {type(error).__name__}"
                ) from error
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
        if future is not None:
            if not future.done():
                future.set_result(response)
            return True

        future = self._pending_named_command
        if future is None:
            return False
        self._pending_named_command = None
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
        """Publish an unsolicited controller message to protocol listeners."""
        for listener in tuple(self._message_listeners):
            try:
                listener(payload)
            except Exception:  # pragma: no cover - defensive listener isolation
                _LOGGER.exception("Error in ZONT message listener")

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
                return await self._async_send_and_wait(
                    ws,
                    {"id": command_id, "cmd": command},
                    future,
                    response_timeout,
                    ZontCommandTimeoutError(f"No response for command {command_id}"),
                    f"object command {command_id}",
                )
            finally:
                if self._pending_commands.get(command_id) is future:
                    self._pending_commands.pop(command_id, None)
                if not future.done():
                    future.cancel()

    async def async_send_named_command(
        self,
        name: str,
        object_type: int,
        command: ZontCommand,
        response_timeout: float = COMMAND_TIMEOUT,
        *,
        object_subtype: int | None = None,
    ) -> dict[str, Any]:
        """Send one object command addressed by name and return its response."""
        if not isinstance(name, str):
            raise ValueError("Object name must be text")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Object name must be non-empty text")
        if type(object_type) is not int or not 0 <= object_type <= 255:
            raise ValueError("Object type must be an integer from 0 to 255")
        if object_subtype is not None and (
            type(object_subtype) is not int or not 0 <= object_subtype <= 255
        ):
            raise ValueError("Object subtype must be an integer from 0 to 255")

        async with self._named_command_lock:
            ws = self._connected_websocket()
            future: asyncio.Future[dict[str, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending_named_command = future
            try:
                payload: dict[str, Any] = {
                    "name": normalized_name,
                    "type": object_type,
                    "cmd": command,
                }
                if object_subtype is not None:
                    payload["stype"] = object_subtype
                return await self._async_send_and_wait(
                    ws,
                    payload,
                    future,
                    response_timeout,
                    ZontCommandTimeoutError(
                        f"No response for named command {normalized_name!r}"
                    ),
                    f"named object command type {object_type}",
                )
            finally:
                if self._pending_named_command is future:
                    self._pending_named_command = None
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
                return await self._async_send_and_wait(
                    ws,
                    {"req_ids": object_type},
                    future,
                    response_timeout,
                    ZontRequestTimeoutError(
                        f"No object ID response for type {object_type}"
                    ),
                    f"object IDs type {object_type}",
                )
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
                return await self._async_send_and_wait(
                    ws,
                    {"id": object_id, "req_state": 0},
                    future,
                    response_timeout,
                    ZontRequestTimeoutError(
                        f"No state response for object {object_id}"
                    ),
                    f"object state {object_id}",
                )
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
                return await self._async_send_and_wait(
                    ws,
                    {"scmd": command},
                    future,
                    response_timeout,
                    ZontRequestTimeoutError("No response for system command"),
                    _system_command_label(command),
                )
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

    async def _async_send_and_wait[T](
        self,
        ws: ClientWebSocketResponse,
        payload: Mapping[str, Any],
        future: asyncio.Future[T],
        response_timeout: float,
        timeout_error: ZontRequestTimeoutError,
        request_label: str,
    ) -> T:
        """Send a request and invalidate its connection on cancellation or timeout."""
        try:
            await self._async_send_payload(ws, payload)
            async with asyncio.timeout(response_timeout):
                return await future
        except asyncio.CancelledError:
            await self._async_invalidate_connection(ws)
            raise
        except ZontConnectionError:
            _LOGGER.debug(
                "ZONT request failed because the connection was lost: %s",
                request_label,
            )
            raise
        except TimeoutError as err:
            _LOGGER.debug(
                "ZONT request timed out after %.1f seconds: %s",
                response_timeout,
                request_label,
            )
            await self._async_invalidate_connection(ws)
            raise timeout_error from err

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

    def _record_error(self, category: str, error: Exception) -> None:
        """Record an outage without repeatedly filling the log."""
        self._last_error = category
        _LOGGER.debug(
            "ZONT WebSocket outage details (%s): %s",
            category,
            _safe_outage_detail(error),
        )
        if not self._outage_logged:
            _LOGGER.warning(
                "ZONT WebSocket connection lost (%s); reconnecting in the background",
                category,
            )
            self._outage_logged = True

    def _set_connected(self, connected: bool) -> None:
        """Update connection state and notify protocol listeners."""
        if self._is_connected == connected:
            return
        self._is_connected = connected
        if connected:
            self._last_error = None
        for listener in tuple(self._connection_listeners):
            try:
                listener(connected)
            except Exception:  # pragma: no cover - defensive listener isolation
                _LOGGER.exception("Error in ZONT connection listener")

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
        if self._pending_named_command is not None:
            pending.append(self._pending_named_command)
            self._pending_named_command = None
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


def _system_command_label(command: str) -> str:
    """Return a diagnostic label without exposing command values."""
    normalized = command.strip()
    if (
        normalized.startswith("#")
        and normalized.endswith("?")
        and normalized[1:-1].isalnum()
    ):
        return f"system command {normalized}"
    return "system command"


def _safe_outage_detail(error: Exception) -> str:
    """Return useful outage details without logging external error text."""
    message = str(error)
    close_prefix = "The WebSocket was closed by the controller (code "
    if message.startswith(close_prefix) and message.endswith(")"):
        close_code = message[len(close_prefix) : -1]
        if close_code == "None" or close_code.isdecimal():
            return f"{type(error).__name__} (close code {close_code})"
    return type(error).__name__
