"""Tests for the ZONT WebSocket client."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import suppress
from typing import Any

import pytest
from aiohttp import ClientError, WSMsgType
from custom_components.zont_local.protocol import (
    ZontAuthenticationError,
    ZontClient,
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    ZontRequestTimeoutError,
    async_open_temporary_request_session,
    async_request_system_commands,
)
from custom_components.zont_local.protocol.controller import (
    async_refresh_controller_info,
)


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


class HangingSession:
    """Block while opening a WebSocket until the caller cancels the attempt."""

    def __init__(self) -> None:
        self.cancelled = False

    async def ws_connect(self, url: str, heartbeat: float) -> FakeWebSocket:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("The WebSocket open should have been cancelled")


class ProtocolTestClient(ZontClient):
    """Track the caller-owned supervisor task for isolated protocol tests."""

    supervisor_task: asyncio.Task[None] | None = None

    async def async_stop(self) -> None:
        await super().async_stop()
        if self.supervisor_task is not None:
            with suppress(asyncio.CancelledError, ZontAuthenticationError):
                await self.supervisor_task


def make_client(
    _hass: Any,
    session: FakeSession,
    url: str,
    credentials: ZontCredentials,
    *_ha_arguments: Any,
) -> ProtocolTestClient:
    """Build the pure client from the former HA-shaped test call sites."""
    return ProtocolTestClient(session, url, credentials)  # type: ignore[arg-type]


async def async_start_client(client: ProtocolTestClient) -> asyncio.Task[None]:
    """Connect and start supervision in a caller-owned task."""
    await client.async_connect()
    client.supervisor_task = asyncio.create_task(client.async_supervise())
    await asyncio.sleep(0)
    return client.supervisor_task


def auth_socket(status: int = 200) -> FakeWebSocket:
    """Return a socket with a queued auth response."""
    return FakeWebSocket([FakeMessage(WSMsgType.TEXT, json.dumps({"auth": status}))])


@pytest.mark.asyncio
async def test_temporary_session_closes_socket() -> None:
    ws = auth_socket()
    async with async_open_temporary_request_session(
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
    ):
        pass
    assert ws.closed
    assert ws.sent == [{"user": "user", "pass": "password"}]


@pytest.mark.asyncio
async def test_temporary_session_rejects_auth_and_closes() -> None:
    ws = auth_socket(403)
    with pytest.raises(ZontAuthenticationError):
        async with async_open_temporary_request_session(
            FakeSession([ws]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "bad"),
        ):
            pass
    assert ws.closed


@pytest.mark.asyncio
async def test_temporary_session_rejects_non_object_auth() -> None:
    ws = FakeWebSocket([FakeMessage(WSMsgType.TEXT, "[]")])
    with pytest.raises(ZontProtocolError):
        async with async_open_temporary_request_session(
            FakeSession([ws]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        ):
            pass
    assert ws.closed


@pytest.mark.asyncio
async def test_temporary_session_wraps_network_failure() -> None:
    with pytest.raises(ZontConnectionError):
        async with async_open_temporary_request_session(
            FakeSession([ClientError("offline")]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        ):
            pass


@pytest.mark.asyncio
async def test_temporary_system_commands_are_serialized() -> None:
    ws = FakeWebSocket(
        [
            FakeMessage(WSMsgType.TEXT, json.dumps({"auth": 200})),
            FakeMessage(WSMsgType.TEXT, json.dumps({"id": 7, "type": 1})),
            FakeMessage(WSMsgType.TEXT, json.dumps({"scmdres": "#S54:abcdef123456"})),
            FakeMessage(
                WSMsgType.TEXT,
                json.dumps({"scmdres": "#S7:H1V02_PRO 700 625"}),
            ),
        ]
    )

    responses = await async_request_system_commands(
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        ("#S54?", "#S7?"),
    )

    assert responses == ["#S54:abcdef123456", "#S7:H1V02_PRO 700 625"]
    assert ws.sent == [
        {"user": "user", "pass": "password"},
        {"scmd": "#S54?"},
        {"scmd": "#S7?"},
    ]
    assert ws.closed


@pytest.mark.asyncio
async def test_temporary_session_reads_ids_and_object_state() -> None:
    ws = FakeWebSocket(
        [
            FakeMessage(WSMsgType.TEXT, json.dumps({"auth": 200})),
            FakeMessage(WSMsgType.TEXT, json.dumps({"id": 7, "type": 1})),
            FakeMessage(WSMsgType.TEXT, json.dumps("CFG_RELOAD_REQ")),
            FakeMessage(WSMsgType.TEXT, json.dumps({"ids": [8362, 20496]})),
            FakeMessage(WSMsgType.TEXT, json.dumps({"id": 8362, "type": 16})),
            FakeMessage(
                WSMsgType.TEXT,
                json.dumps(
                    {
                        "id": 20496,
                        "type": 16,
                        "stype": 3,
                        "name": "Радиаторы",
                    }
                ),
            ),
        ]
    )

    async with async_open_temporary_request_session(
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
    ) as requests:
        ids = await requests.async_get_object_ids(16)
        state = await requests.async_get_object_state(20496)

    assert ids == [8362, 20496]
    assert state["name"] == "Радиаторы"
    assert ws.sent == [
        {"user": "user", "pass": "password"},
        {"req_ids": 16},
        {"id": 20496, "req_state": 0},
    ]
    assert ws.closed


@pytest.mark.asyncio
async def test_connection_timeout_covers_websocket_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = HangingSession()
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.session.CONNECTION_TIMEOUT", 0.01
    )

    with pytest.raises(ZontConnectionError) as raised:
        async with async_open_temporary_request_session(
            session,  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        ):
            pass

    assert isinstance(raised.value.__cause__, TimeoutError)
    assert session.cancelled


@pytest.mark.asyncio
async def test_connection_timeout_covers_authentication_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWebSocket()
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.session.CONNECTION_TIMEOUT", 0.01
    )

    with pytest.raises(ZontConnectionError) as raised:
        async with async_open_temporary_request_session(
            FakeSession([ws]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        ):
            pass

    assert isinstance(raised.value.__cause__, TimeoutError)
    assert ws.closed


async def test_supervisor_is_owned_by_the_caller(auth_error_callback: Any) -> None:
    """Keep protocol supervision in an explicit caller-owned task."""
    ws = auth_socket()
    client = make_client(
        None,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        auth_error_callback,
    )

    supervisor = await async_start_client(client)

    assert not supervisor.done()
    await client.async_stop()
    assert supervisor.done()


@pytest.mark.asyncio
async def test_unsolicited_payloads_are_wrapped(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    client = make_client(
        fake_hass,
        FakeSession([]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    received: list[Any] = []
    client.async_add_message_listener(received.append)

    client._handle_incoming("[1, 2]")
    client._handle_incoming("not-json")

    assert received == [[1, 2], "not-json"]


@pytest.mark.asyncio
async def test_unsolicited_payloads_notify_internal_listeners(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    """Expose push messages only to registered protocol listeners."""
    client = make_client(
        fake_hass,
        FakeSession([]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    received: list[Any] = []
    unsubscribe = client.async_add_message_listener(received.append)

    client._handle_incoming('{"id":7,"type":14,"s":1}')
    unsubscribe()
    client._handle_incoming('{"id":7,"type":14,"s":0}')

    assert received == [{"id": 7, "type": 14, "s": 1}]


@pytest.mark.asyncio
async def test_client_reconnects_after_initial_setup(
    fake_hass: Any,
    auth_error_callback: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_ws = auth_socket()
    second_ws = auth_socket()
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.client.RECONNECT_DELAYS", (0,)
    )
    client = make_client(
        fake_hass,
        FakeSession([first_ws, second_ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )

    await async_start_client(client)
    assert client.is_connected
    assert client.reconnect_count == 0

    await first_ws.close()
    for _ in range(20):
        if client.reconnect_count == 1:
            break
        await asyncio.sleep(0)

    assert client.is_connected
    assert client.reconnect_count == 1
    await client.async_stop()


@pytest.mark.asyncio
async def test_client_retries_a_recoverable_reconnect_failure(
    fake_hass: Any,
    auth_error_callback: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_ws = auth_socket()
    replacement_ws = auth_socket()
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.client.RECONNECT_DELAYS", (0,)
    )
    client = make_client(
        fake_hass,
        FakeSession([first_ws, ClientError("offline"), replacement_ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )

    await async_start_client(client)
    await first_ws.close()
    for _ in range(30):
        if client.reconnect_count == 1:
            break
        await asyncio.sleep(0)

    assert client.is_connected
    assert client.last_error is None
    assert client.reconnect_count == 1
    await client.async_stop()


@pytest.mark.asyncio
async def test_reconnect_authentication_failure_ends_supervision(
    fake_hass: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_ws = auth_socket()
    rejected_ws = auth_socket(401)
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.client.RECONNECT_DELAYS", (0,)
    )
    client = make_client(
        fake_hass,
        FakeSession([first_ws, rejected_ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        None,
    )

    supervisor = await async_start_client(client)
    await first_ws.close()
    for _ in range(20):
        if supervisor.done():
            break
        await asyncio.sleep(0)

    assert not client.is_connected
    assert client.last_error == "authentication"
    assert rejected_ws.closed
    assert isinstance(supervisor.exception(), ZontAuthenticationError)
    await client.async_stop()


@pytest.mark.asyncio
async def test_send_failure_cleans_pending(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)
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
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    with pytest.raises(ZontCommandTimeoutError):
        await client.async_send_command(42, "value", response_timeout=0.01)

    assert client.pending_count == 0
    assert ws.closed
    await client.async_stop()


@pytest.mark.asyncio
async def test_command_response_is_correlated(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)
    client._handle_incoming('{"id":7,"cmdres":0}')

    assert await command_task == {"id": 7, "cmdres": 0}
    assert client.pending_count == 0
    await client.async_stop()


@pytest.mark.asyncio
async def test_numeric_command_is_serialized_without_string_conversion(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    command_task = asyncio.create_task(client.async_send_command(8362, 3330))
    await asyncio.sleep(0)

    assert ws.sent[-1] == {"id": 8362, "cmd": 3330}
    client._handle_incoming('{"id":8362,"cmdres":0}')
    assert await command_task == {"id": 8362, "cmdres": 0}
    await client.async_stop()


@pytest.mark.asyncio
async def test_named_command_is_serialized_and_correlated(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    command_task = asyncio.create_task(
        client.async_send_named_command("HA - Кабинет", 1, "1 24.1")
    )
    await asyncio.sleep(0)

    assert ws.sent[-1] == {
        "name": "HA - Кабинет",
        "type": 1,
        "cmd": "1 24.1",
    }
    assert client.pending_count == 1
    client._handle_incoming('{"id":4111,"cmdres":0}')

    assert await command_task == {"id": 4111, "cmdres": 0}
    assert client.pending_count == 0
    await client.async_stop()


@pytest.mark.asyncio
async def test_named_command_includes_optional_object_subtype(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    command_task = asyncio.create_task(
        client.async_send_named_command(
            "HA - Дверь",
            0,
            "0 0 180",
            object_subtype=20,
        )
    )
    await asyncio.sleep(0)

    assert ws.sent[-1] == {
        "name": "HA - Дверь",
        "type": 0,
        "stype": 20,
        "cmd": "0 0 180",
    }
    client._handle_incoming('{"id":4116,"cmdres":0}')
    assert await command_task == {"id": 4116, "cmdres": 0}
    await client.async_stop()


@pytest.mark.asyncio
async def test_addressed_response_does_not_complete_named_command(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    named = asyncio.create_task(client.async_send_named_command("HA - Кабинет", 1, 24))
    addressed = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)

    client._handle_incoming('{"id":7,"cmdres":0}')
    assert await addressed == {"id": 7, "cmdres": 0}
    assert not named.done()

    client._handle_incoming('{"id":4111,"cmdres":0}')
    assert (await named)["id"] == 4111
    await client.async_stop()


@pytest.mark.asyncio
async def test_named_command_timeout_invalidates_connection(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    with pytest.raises(ZontCommandTimeoutError):
        await client.async_send_named_command(
            "HA - Кабинет", 1, "1 24.1", response_timeout=0.01
        )

    assert client.pending_count == 0
    assert ws.closed
    await client.async_stop()


@pytest.mark.asyncio
async def test_push_state_does_not_complete_command(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)
    received: list[Any] = []
    client.async_add_message_listener(received.append)

    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)
    client._handle_incoming('{"id":7,"type":14,"s":1}')

    assert not command_task.done()
    assert received == [{"id": 7, "type": 14, "s": 1}]

    client._handle_incoming('{"id":7,"cmdres":0}')
    assert await command_task == {"id": 7, "cmdres": 0}
    await client.async_stop()


@pytest.mark.asyncio
async def test_uppercase_command_id_is_supported(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)
    client._handle_incoming('{"Id":7,"cmdres":0}')

    assert await command_task == {"Id": 7, "cmdres": 0}
    await client.async_stop()


@pytest.mark.asyncio
async def test_conflicting_command_ids_do_not_complete_command(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)
    client._handle_incoming('{"id":7,"Id":8,"cmdres":0}')

    assert not command_task.done()
    client._handle_incoming('{"id":7,"cmdres":0}')
    assert await command_task == {"id": 7, "cmdres": 0}
    await client.async_stop()


@pytest.mark.asyncio
async def test_cancelled_command_cleans_pending(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)
    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    await asyncio.sleep(0)

    command_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command_task

    assert client.pending_count == 0
    assert ws.closed
    await client.async_stop()


@pytest.mark.asyncio
async def test_same_ids_are_serialized(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)
    first = asyncio.create_task(client.async_send_command(7, "first"))
    second = asyncio.create_task(client.async_send_command(7, "second"))
    await asyncio.sleep(0)

    assert client.pending_count == 1
    client._handle_incoming('{"id":7,"cmdres":1}')
    assert await first == {"id": 7, "cmdres": 1}
    await asyncio.sleep(0)
    client._handle_incoming('{"id":7,"cmdres":2}')
    assert await second == {"id": 7, "cmdres": 2}
    await client.async_stop()


@pytest.mark.asyncio
async def test_different_ids_can_be_in_flight(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)
    first = asyncio.create_task(client.async_send_command(7, "first"))
    second = asyncio.create_task(client.async_send_command(8, "second"))
    await asyncio.sleep(0)

    assert client.pending_count == 2
    client._handle_incoming('{"id":8,"cmdres":0}')
    client._handle_incoming('{"id":7,"cmdres":0}')
    assert (await first)["id"] == 7
    assert (await second)["id"] == 8
    await client.async_stop()


@pytest.mark.asyncio
async def test_command_and_state_for_same_id_are_routed_independently(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    command_task = asyncio.create_task(client.async_send_command(7, "1"))
    state_task = asyncio.create_task(client.async_get_object_state(7))
    await asyncio.sleep(0)

    assert ws.sent[-2:] == [{"id": 7, "cmd": "1"}, {"id": 7, "req_state": 0}]
    client._handle_incoming('{"id":7,"type":14,"s":1}')
    assert await state_task == {"id": 7, "type": 14, "s": 1}
    assert not command_task.done()

    client._handle_incoming('{"id":7,"cmdres":0}')
    assert await command_task == {"id": 7, "cmdres": 0}
    await client.async_stop()


@pytest.mark.asyncio
async def test_object_id_requests_are_serialized(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    first = asyncio.create_task(client.async_get_object_ids(6))
    second = asyncio.create_task(client.async_get_object_ids(255))
    await asyncio.sleep(0)

    assert ws.sent[-1] == {"req_ids": 6}
    client._handle_incoming('{"ids":[4097]}')
    assert await first == [4097]

    await asyncio.sleep(0)
    assert ws.sent[-1] == {"req_ids": 255}
    client._handle_incoming('{"ids":[4097,9078]}')
    assert await second == [4097, 9078]

    empty = asyncio.create_task(client.async_get_object_ids(8))
    await asyncio.sleep(0)
    client._handle_incoming('{"ids":[]}')
    assert await empty == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_invalid_object_id_response_raises_protocol_error(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    request = asyncio.create_task(client.async_get_object_ids(16))
    await asyncio.sleep(0)
    client._handle_incoming('{"ids":[20496,true]}')

    with pytest.raises(ZontProtocolError):
        await request
    assert client.is_connected
    await client.async_stop()


@pytest.mark.asyncio
async def test_object_id_timeout_invalidates_connection(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    with pytest.raises(ZontRequestTimeoutError):
        await client.async_get_object_ids(16, response_timeout=0.01)

    assert ws.closed
    await client.async_stop()


@pytest.mark.asyncio
async def test_state_failed_response_is_returned(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    request = asyncio.create_task(client.async_get_object_state(20494))
    await asyncio.sleep(0)
    client._handle_incoming('{"id":20494,"req_state":0,"failed":1}')

    assert await request == {"id": 20494, "req_state": 0, "failed": 1}
    await client.async_stop()


@pytest.mark.asyncio
async def test_state_timeout_invalidates_connection(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    with pytest.raises(ZontRequestTimeoutError):
        await client.async_get_object_state(20494, response_timeout=0.01)

    assert ws.closed
    await client.async_stop()


@pytest.mark.asyncio
async def test_same_state_ids_are_serialized(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    first = asyncio.create_task(client.async_get_object_state(7))
    second = asyncio.create_task(client.async_get_object_state(7))
    await asyncio.sleep(0)
    assert ws.sent.count({"id": 7, "req_state": 0}) == 1

    client._handle_incoming('{"id":7,"type":14,"s":0}')
    assert (await first)["s"] == 0
    await asyncio.sleep(0)
    assert ws.sent.count({"id": 7, "req_state": 0}) == 2

    client._handle_incoming('{"id":7,"type":14,"s":1}')
    assert (await second)["s"] == 1
    await client.async_stop()


@pytest.mark.asyncio
async def test_system_command_requests_are_serialized(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    first = asyncio.create_task(client.async_send_system_command("#S7?"))
    second = asyncio.create_task(client.async_send_system_command("SDATE?"))
    await asyncio.sleep(0)

    assert ws.sent[-1] == {"scmd": "#S7?"}
    client._handle_incoming('{"scmdres":"#S7:H1V02_PRO 700 625"}')
    assert await first == "#S7:H1V02_PRO 700 625"

    await asyncio.sleep(0)
    assert ws.sent[-1] == {"scmd": "SDATE?"}
    client._handle_incoming('{"scmdres":"SDATE=8 8 26 10 14 19"}')
    assert await second == "SDATE=8 8 26 10 14 19"
    await client.async_stop()


@pytest.mark.asyncio
async def test_system_command_can_keep_connection_without_waiting_for_response(
    fake_hass: Any,
    auth_error_callback: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the connection to the controller after a fire-and-forget command."""
    first_ws = auth_socket()
    second_ws = auth_socket()
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.client.RECONNECT_DELAYS", (0,)
    )
    client = make_client(
        fake_hass,
        FakeSession([first_ws, second_ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    await client.async_send_system_command_without_response("SRESTART?")

    assert first_ws.sent[-1] == {"scmd": "SRESTART?"}
    assert not first_ws.closed
    assert client.is_connected

    client._handle_incoming('{"scmdres":"SRESTART:OK"}')

    assert fake_hass.bus.events == []
    assert client.is_connected

    await first_ws.close()
    for _ in range(20):
        if client.reconnect_count == 1:
            break
        await asyncio.sleep(0)

    assert client.is_connected
    assert client.reconnect_count == 1
    await client.async_stop()


@pytest.mark.asyncio
async def test_controller_information_is_parsed(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    request = asyncio.create_task(async_refresh_controller_info(client, "ABCDEF123456"))
    await asyncio.sleep(0)
    assert ws.sent[-1] == {"scmd": "#S7?"}
    client._handle_incoming('{"scmdres":"#S7:H1V02_PRO 700 625"}')

    info = await request
    assert info.model == "H1V02 PRO"
    assert info.board_model == "700"
    assert info.firmware_version == "625"
    await client.async_stop()


@pytest.mark.asyncio
async def test_invalid_system_response_raises_protocol_error(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    request = asyncio.create_task(client.async_send_system_command("#S7?"))
    await asyncio.sleep(0)
    client._handle_incoming('{"scmdres":7}')

    with pytest.raises(ZontProtocolError):
        await request
    await client.async_stop()


@pytest.mark.asyncio
async def test_system_command_timeout_invalidates_connection(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)

    with pytest.raises(ZontRequestTimeoutError):
        await client.async_send_system_command("#S7?", response_timeout=0.01)

    assert ws.closed
    await client.async_stop()


@pytest.mark.asyncio
async def test_unsolicited_system_response_is_not_exposed(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    client = make_client(
        fake_hass,
        FakeSession([]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    received: list[Any] = []
    client.async_add_message_listener(received.append)

    client._handle_incoming('{"scmdres":"#S208:sensitive"}')

    assert received == []


@pytest.mark.asyncio
async def test_unmatched_protocol_responses_are_published(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    client = make_client(
        fake_hass,
        FakeSession([]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    received: list[Any] = []
    client.async_add_message_listener(received.append)

    client._handle_incoming('{"id":7,"cmdres":0}')
    client._handle_incoming('{"ids":[4097]}')

    assert received == [{"id": 7, "cmdres": 0}, {"ids": [4097]}]


@pytest.mark.asyncio
async def test_stop_cleans_pending_and_task(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    ws = auth_socket()
    client = make_client(
        fake_hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        "entry",
        "device",
        auth_error_callback,
    )
    await async_start_client(client)
    command_task = asyncio.create_task(client.async_send_command(7, "value"))
    state_task = asyncio.create_task(client.async_get_object_state(8))
    ids_task = asyncio.create_task(client.async_get_object_ids(255))
    scmd_task = asyncio.create_task(client.async_send_system_command("#S7?"))
    await asyncio.sleep(0)

    await client.async_stop()

    for task in (command_task, state_task, ids_task, scmd_task):
        with pytest.raises(ZontConnectionError):
            await task
    assert client.pending_count == 0
    assert ws.closed
