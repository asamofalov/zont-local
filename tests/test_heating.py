"""Tests for ZONT heating circuit commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.heating import (
    ZontCommandRejectedError,
    async_set_heating_circuit_temperature,
    celsius_to_decikelvin,
)


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [(0, 2730), (21.5, 2945), (40, 3130), (60, 3330), (100, 3730)],
)
def test_celsius_to_decikelvin(temperature: float, expected: int) -> None:
    assert celsius_to_decikelvin(temperature) == expected


@pytest.mark.parametrize("temperature", [float("nan"), float("inf")])
def test_celsius_to_decikelvin_rejects_non_finite(temperature: float) -> None:
    with pytest.raises(ValueError):
        celsius_to_decikelvin(temperature)


async def test_set_temperature_sends_numeric_command() -> None:
    client = MagicMock(spec=ZontWsClient)
    client.async_send_command = AsyncMock(return_value={"id": 8362, "cmdres": 0})

    await async_set_heating_circuit_temperature(client, 8362, 60)

    client.async_send_command.assert_awaited_once_with(8362, 3330)


@pytest.mark.parametrize("result", [1, True, None, "0"])
async def test_set_temperature_rejects_unsuccessful_response(result: object) -> None:
    client = MagicMock(spec=ZontWsClient)
    client.async_send_command = AsyncMock(return_value={"id": 8362, "cmdres": result})

    with pytest.raises(ZontCommandRejectedError) as raised:
        await async_set_heating_circuit_temperature(client, 8362, 60)

    assert raised.value.result == result
