"""Short-lived authenticated ZONT WebSocket sessions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from typing import Any

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType

from .constants import CONNECTION_TIMEOUT, REQUEST_TIMEOUT, WS_HEARTBEAT
from .errors import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from .types import ZontCredentials

_LOGGER = logging.getLogger(__name__)


async def async_close_websocket(ws: ClientWebSocketResponse | None) -> None:
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
    except Exception as err:  # pragma: no cover - aiohttp close is best effort
        _LOGGER.debug(
            "Error while closing ZONT WebSocket (%s)",
            type(err).__name__,
        )


async def async_open_websocket(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
) -> ClientWebSocketResponse:
    """Open and authenticate a WebSocket connection."""
    ws: ClientWebSocketResponse | None = None
    try:
        async with asyncio.timeout(CONNECTION_TIMEOUT):
            ws = await session.ws_connect(url, heartbeat=WS_HEARTBEAT)
            await ws.send_str(
                json.dumps(
                    {"user": credentials.username, "pass": credentials.password},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
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
        await async_close_websocket(ws)
        raise
    except (ZontAuthenticationError, ZontProtocolError):
        await async_close_websocket(ws)
        raise
    except (TimeoutError, ClientError, OSError) as err:
        await async_close_websocket(ws)
        raise ZontConnectionError("Unable to connect to the controller") from err
    except Exception as err:
        await async_close_websocket(ws)
        raise ZontConnectionError("Unable to open the WebSocket") from err


class ZontTemporaryRequestSession:
    """Run serialized requests on a short-lived authenticated connection."""

    def __init__(self, ws: ClientWebSocketResponse) -> None:
        self._ws = ws

    async def async_get_object_ids(
        self, object_type: int, response_timeout: float = REQUEST_TIMEOUT
    ) -> list[int]:
        """Return object identifiers for one type."""
        if type(object_type) is not int or not 0 <= object_type <= 255:
            raise ValueError("Object type must be an integer from 0 to 255")
        await self._send({"req_ids": object_type})
        response = await self._receive_matching(
            lambda payload: "ids" in payload, response_timeout
        )
        ids = response["ids"]
        if not isinstance(ids, list) or any(
            type(object_id) is not int or object_id < 0 for object_id in ids
        ):
            raise ZontProtocolError("Object ID response is invalid")
        return ids

    async def async_get_object_state(
        self, object_id: int, response_timeout: float = REQUEST_TIMEOUT
    ) -> dict[str, Any]:
        """Return the current state of one object."""
        if type(object_id) is not int or object_id < 0:
            raise ValueError("Object ID must be a non-negative integer")
        await self._send({"id": object_id, "req_state": 0})
        response = await self._receive_matching(
            lambda payload: payload.get("id") == object_id and "cmdres" not in payload,
            response_timeout,
        )
        return dict(response)

    async def async_send_system_command(
        self, command: str, response_timeout: float = REQUEST_TIMEOUT
    ) -> str:
        """Send one system command and return its text result."""
        if not isinstance(command, str) or not command.strip():
            raise ValueError("System command must be non-empty text")
        await self._send({"scmd": command})
        response = await self._receive_matching(
            lambda payload: "scmdres" in payload, response_timeout
        )
        result = response["scmdres"]
        if not isinstance(result, str):
            raise ZontProtocolError("System command response is not text")
        return result

    async def _send(self, payload: Mapping[str, Any]) -> None:
        try:
            await self._ws.send_str(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
        except (ClientError, OSError, RuntimeError) as err:
            raise ZontConnectionError("Unable to send controller request") from err

    async def _receive_matching(
        self,
        matcher: Callable[[Mapping[str, Any]], bool],
        response_timeout: float,
    ) -> Mapping[str, Any]:
        try:
            async with asyncio.timeout(response_timeout):
                while True:
                    message = await self._ws.receive()
                    if message.type is WSMsgType.TEXT:
                        try:
                            response = json.loads(message.data)
                        except (TypeError, json.JSONDecodeError) as err:
                            raise ZontProtocolError(
                                "Controller response is not JSON"
                            ) from err
                        if isinstance(response, Mapping) and matcher(response):
                            return response
                        continue
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
            raise ZontRequestTimeoutError("No controller response") from err
        except (ClientError, OSError, RuntimeError) as err:
            raise ZontConnectionError("Unable to request controller data") from err


@asynccontextmanager
async def async_open_temporary_request_session(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
) -> AsyncIterator[ZontTemporaryRequestSession]:
    """Open and close a serialized temporary request session."""
    ws = await async_open_websocket(session, url, credentials)
    try:
        yield ZontTemporaryRequestSession(ws)
    finally:
        await async_close_websocket(ws)


async def async_request_system_commands(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
    commands: tuple[str, ...],
    response_timeout: float = REQUEST_TIMEOUT,
) -> list[str]:
    """Run serialized system commands on one temporary connection."""
    async with async_open_temporary_request_session(
        session, url, credentials
    ) as request_session:
        return [
            await request_session.async_send_system_command(command, response_timeout)
            for command in commands
        ]
