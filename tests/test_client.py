"""Tests for the ZONT WebSocket client."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

import pytest
from aiohttp import ClientError, WSMsgType
from custom_components.zont_ws.client import (
    ZontAuthenticationError,
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    ZontWsClient,
    async_validate_connection,
)
from custom_components.zont_ws.const import EVENT_MESSAGE


class FakeMessage:
    """Simple aiohttp WebSocket message replacement."""

    def __init__(self, message_type: WSMsgType, data: Any = None) -> None:
        self.type = message_type
        self.data = data


class FakeWebSocket:
    """Scriptable WebSocket for client tests."""

    def __init__(self, messages: list[FakeMessage] | None = None) -> None:
        self.messages = deque(messages or [])
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.send_error: Exception | None = None
        self.receive_waiter = asyncio.Event()

    async def send_str(self, raw: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(json.loads(raw))

    async def receive(self, timeout: float | None = None) -> FakeMessage:
        if self.messages:
            return self.messages.popleft()
        await self.receive_waiter.wait()
        return FakeMessage(WSMsgType.CLOSED)

    async def close(self) -> None:
        self.closed = True
        self.receive_waiter.set()


class FakeSession:
    """Return scripted WebSockets from ws_connect."""

    def __init__(self, sockets: list[FakeWebSocket | Exception]) -> None:
        self.sockets = deque(sockets)

    async def ws_connect(self, url: str, heartbeat: float) -> FakeWebSocket:
        result = self.sockets.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def auth_socket(status: int = 200) -> FakeWebSocket:
    """Return a socket with a queued auth response."""
    return FakeWebSocket([FakeMessage(WSMsgType.TEXT, json.dumps({"auth": status}))])


@pytest.mark.asyncio
async def test_validate_connection_closes_socket() -> None:
    ws = auth_socket()
    await async_validate_connection(
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
    )
    assert ws.closed
    assert ws.sent == [{"user": "user", "pass": "password"}]


@pytest.mark.asyncio
async def test_validate_connection_rejects_auth_and_closes() -> None:
    ws = auth_socket(403)
    with pytest.raises(ZontAuthenticationError):
        await async_validate_connection(
            FakeSession([ws]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "bad"),
        )
    assert ws.closed


@pytest.mark.asyncio
async def test_validate_connection_rejects_non_object_auth() -> None:
    ws = FakeWebSocket([FakeMessage(WSMsgType.TEXT, "[]")])
    with pytest.raises(ZontProtocolError):
        await async_validate_connection(
            FakeSession([ws]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        )
    assert ws.closed


@pytest.mark.asyncio
async def test_validate_connection_wraps_network_failure() -> None:
    with pytest.raises(ZontConnectionError):
        await async_validate_connection(
            FakeSession([ClientError("offline")]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        )


@pytest.mark.asyncio
async def test_unsolicited_payloads_are_wrapped(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    client = ZontWsClient(
        fake_hass,
        FakeSession([]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )

    client._handle_incoming("[1, 2]")
    client._handle_incoming("not-json")

    assert fake_hass.bus.events == [
        (EVENT_MESSAGE, {"device_id": "device", "payload": [1, 2]}),
        (EVENT_MESSAGE, {"device_id": "device", "payload": "not-json"}),
    ]


@pytest.mark.asyncio
async def test_send_failure_cleans_pending(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = ZontWsClient(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await client.async_start()
    ws.send_error = ClientError("send failed")

    with pytest.raises(ZontConnectionError):
        await client.async_send_command(42, "value")

    assert client.pending_count == 0
    await client.async_stop()


@pytest.mark.asyncio
async def test_command_timeout_cleans_pending(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = ZontWsClient(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await client.async_start()

    with pytest.raises(ZontCommandTimeoutError):
        await client.async_send_command(42, "value", response_timeout=0.01)

    assert client.pending_count == 0
    await client.async_stop()


@pytest.mark.asyncio
async def test_command_response_is_correlated(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = ZontWsClient(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await client.async_start()

    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)
    client._handle_incoming('{"id":7,"status":"ok"}')

    assert await command_task == {"id": 7, "status": "ok"}
    assert client.pending_count == 0
    await client.async_stop()


@pytest.mark.asyncio
async def test_cancelled_command_cleans_pending(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = ZontWsClient(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await client.async_start()
    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)

    command_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command_task

    assert client.pending_count == 0
    await client.async_stop()


@pytest.mark.asyncio
async def test_same_ids_are_serialized(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = ZontWsClient(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await client.async_start()
    first = asyncio.create_task(client.async_send_command(7, "first"))
    second = asyncio.create_task(client.async_send_command(7, "second"))
    await asyncio.sleep(0)

    assert client.pending_count == 1
    client._handle_incoming('{"id":7,"sequence":1}')
    assert await first == {"id": 7, "sequence": 1}
    await asyncio.sleep(0)
    client._handle_incoming('{"id":7,"sequence":2}')
    assert await second == {"id": 7, "sequence": 2}
    await client.async_stop()


@pytest.mark.asyncio
async def test_different_ids_can_be_in_flight(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = ZontWsClient(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await client.async_start()
    first = asyncio.create_task(client.async_send_command(7, "first"))
    second = asyncio.create_task(client.async_send_command(8, "second"))
    await asyncio.sleep(0)

    assert client.pending_count == 2
    client._handle_incoming('{"id":8,"status":"ok"}')
    client._handle_incoming('{"id":7,"status":"ok"}')
    assert (await first)["id"] == 7
    assert (await second)["id"] == 8
    await client.async_stop()


@pytest.mark.asyncio
async def test_stop_cleans_pending_and_task(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = ZontWsClient(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await client.async_start()
    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)

    await client.async_stop()

    with pytest.raises(ZontConnectionError):
        await command_task
    assert client.pending_count == 0
    assert ws.closed
