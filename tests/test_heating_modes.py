"""Tests for ZONT heating-mode discovery and safety rules."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from custom_components.zont_ws.client import (
    ZontCredentials,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from custom_components.zont_ws.heating_config import (
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
)
from custom_components.zont_ws.heating_modes import (
    ZontHeatingModeDiscovery,
    async_discover_heating_modes,
    async_discover_heating_modes_from_requests,
)
from custom_components.zont_ws.objects import ZontHeatingCircuitData


def _circuit(object_id: int, subtype: int) -> ZontHeatingCircuitData:
    return ZontHeatingCircuitData(
        object_id=object_id,
        object_type=16,
        name="ГВС" if subtype == 1 else "Радиаторы",
        subtype=subtype,
    )


def test_only_fully_zero_and_applicable_mode_is_eligible() -> None:
    discovery = ZontHeatingModeDiscovery(
        circuits={8362: _circuit(8362, 1), 20496: _circuit(20496, 3)},
        states={
            8362: ZontHeatingCircuitInternalState(8362, 4097, 0, (20503, 20504)),
            20496: ZontHeatingCircuitInternalState(20496, 4104, 0, (20503, 20504)),
        },
        modes={
            20503: ZontHeatingModeConfiguration(
                20503,
                "Лето",
                {8362: 3330, 20496: 0},
            ),
            20504: ZontHeatingModeConfiguration(
                20504,
                "Выключен",
                {8362: 0, 20496: 0},
            ),
            20505: ZontHeatingModeConfiguration(
                20505,
                "Неприменим",
                {8362: 0, 20496: 0},
            ),
        },
    )

    assert [mode.object_id for mode in discovery.eligible_off_modes] == [20504]


async def test_discovery_reads_relevant_circuits_and_modes(monkeypatch) -> None:
    requests = AsyncMock()
    requests.async_get_object_ids.side_effect = [[20494, 8362, 20496], [20504]]
    requests.async_get_object_state.side_effect = [
        {"id": 20494, "req_state": 0, "failed": 1},
        {
            "id": 8362,
            "type": 16,
            "stype": 1,
            "name": "ГВС",
            "c": 29,
            "s": 60,
            "m": "heat",
        },
        {
            "id": 20496,
            "type": 16,
            "stype": 3,
            "name": "Радиаторы",
            "c": 42,
            "s": 41,
            "m": "heat",
        },
    ]
    requests.async_send_system_command.side_effect = [
        "#Y8362$3330,3330,[],0,0,20501,4097,0,[20504],0,0",
        "#Y20496$3160,3140,[],0,0,0,4104,0,[20504],0,0",
        "#Z20504:20,'Выключен',[8362,20496],[0,0],[0,0],29,[0,0],10,0,10,4,0",
    ]
    opened = 0

    @asynccontextmanager
    async def open_session(*args, **kwargs):
        nonlocal opened
        opened += 1
        yield requests

    monkeypatch.setattr(
        "custom_components.zont_ws.heating_modes.async_open_temporary_request_session",
        open_session,
    )

    discovery = await async_discover_heating_modes(
        AsyncMock(),  # type: ignore[arg-type]
        "ws://controller/ws",
        ZontCredentials("user", "password"),
    )

    assert set(discovery.circuits) == {8362, 20496}
    assert [mode.object_id for mode in discovery.eligible_off_modes] == [20504]
    assert opened == 1
    assert requests.async_get_object_state.await_args_list[0].args == (20494,)
    assert all(
        call.args != ("#Y20494?",)
        for call in requests.async_send_system_command.await_args_list
    )
    assert requests.async_send_system_command.await_args_list[0].args == ("#Y8362?",)
    assert requests.async_send_system_command.await_args_list[-1].args == ("#Z20504?",)


async def test_discovery_reports_request_timeout_as_protocol_error() -> None:
    requests = AsyncMock()
    requests.async_get_object_ids.side_effect = ZontRequestTimeoutError

    with pytest.raises(ZontProtocolError, match="discovery timed out"):
        await async_discover_heating_modes_from_requests(requests)
