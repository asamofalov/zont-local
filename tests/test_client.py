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
    ZontRequestTimeoutError,
    ZontWsClient,
    async_open_temporary_request_session,
    async_request_system_commands,
    async_validate_connection,
)
from custom_components.zont_ws.const import DOMAIN, EVENT_MESSAGE
from custom_components.zont_ws.controller import async_refresh_controller_info
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


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


class FakeConfigEntry:
    """Create background tasks without involving Home Assistant startup tracking."""

    def __init__(self) -> None:
        self.background_tasks: set[asyncio.Task[Any]] = set()

    def async_create_background_task(
        self,
        hass: Any,
        target: Any,
        name: str,
        eager_start: bool = True,
    ) -> asyncio.Task[Any]:
        """Create and track a task like ConfigEntry."""
        task = asyncio.create_task(target, name=name)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task


async def async_start_client(client: ZontWsClient) -> FakeConfigEntry:
    """Start a client with a config-entry-owned background task."""
    entry = FakeConfigEntry()
    await client.async_start(entry)  # type: ignore[arg-type]
    return entry


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
    monkeypatch.setattr("custom_components.zont_ws.client.CONNECTION_TIMEOUT", 0.01)

    with pytest.raises(ZontConnectionError) as raised:
        await async_validate_connection(
            session,  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        )

    assert isinstance(raised.value.__cause__, TimeoutError)
    assert session.cancelled


@pytest.mark.asyncio
async def test_connection_timeout_covers_authentication_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWebSocket()
    monkeypatch.setattr("custom_components.zont_ws.client.CONNECTION_TIMEOUT", 0.01)

    with pytest.raises(ZontConnectionError) as raised:
        await async_validate_connection(
            FakeSession([ws]),  # type: ignore[arg-type]
            "ws://controller/ws",
            ZontCredentials("user", "password"),
        )

    assert isinstance(raised.value.__cause__, TimeoutError)
    assert ws.closed


async def test_supervisor_does_not_block_home_assistant_startup(
    hass: HomeAssistant,
    auth_error_callback: Any,
) -> None:
    """Keep the long-lived supervisor outside startup task tracking."""
    ws = auth_socket()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    client = ZontWsClient(
        hass,
        FakeSession([ws]),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
        entry.entry_id,
        "device",
        auth_error_callback,
    )

    await client.async_start(entry)
    supervisor = client._supervisor_task

    assert supervisor is not None
    async with asyncio.timeout(0.5):
        await hass.async_block_till_done()
    assert not supervisor.done()

    await client.async_stop()
    assert supervisor.done()


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
async def test_unsolicited_payloads_notify_internal_listeners(
    fake_hass: Any, auth_error_callback: Any
) -> None:
    """Expose push messages internally while preserving the public event."""
    client = ZontWsClient(
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
    assert len(fake_hass.bus.events) == 2


@pytest.mark.asyncio
async def test_client_reconnects_after_initial_setup(
    fake_hass: Any,
    auth_error_callback: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_ws = auth_socket()
    second_ws = auth_socket()
    monkeypatch.setattr("custom_components.zont_ws.client.RECONNECT_DELAYS", (0,))
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
async def test_addressed_response_does_not_complete_named_command(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client._handle_incoming('{"id":7,"type":14,"s":1}')

    assert not command_task.done()
    assert fake_hass.bus.events == [
        (
            EVENT_MESSAGE,
            {"device_id": "device", "payload": {"id": 7, "type": 14, "s": 1}},
        )
    ]

    client._handle_incoming('{"id":7,"cmdres":0}')
    assert await command_task == {"id": 7, "cmdres": 0}
    await client.async_stop()


@pytest.mark.asyncio
async def test_uppercase_command_id_is_supported(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    monkeypatch.setattr("custom_components.zont_ws.client.RECONNECT_DELAYS", (0,))
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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
    client = ZontWsClient(
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

    assert fake_hass.bus.events == []
    assert received == []


@pytest.mark.asyncio
async def test_unmatched_protocol_responses_are_published(
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

    client._handle_incoming('{"id":7,"cmdres":0}')
    client._handle_incoming('{"ids":[4097]}')

    assert fake_hass.bus.events == [
        (
            EVENT_MESSAGE,
            {"device_id": "device", "payload": {"id": 7, "cmdres": 0}},
        ),
        (
            EVENT_MESSAGE,
            {"device_id": "device", "payload": {"ids": [4097]}},
        ),
    ]


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
