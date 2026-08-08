"""Tests for ZONT service actions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_ws.client import ZontConnectionError, ZontWsClient
from custom_components.zont_ws.const import (
    DOMAIN,
    SERVICE_SEND_BULK,
    SERVICE_SEND_COMMAND,
)
from custom_components.zont_ws.services import async_setup_services
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
}


def _loaded_entry(hass: HomeAssistant) -> tuple[MockConfigEntry, MagicMock]:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    client = MagicMock(spec=ZontWsClient)
    client.async_send_command = AsyncMock(return_value={"id": 7, "cmdres": 0})
    entry.runtime_data = client
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry, client


async def test_service_without_entry_is_validation_error(hass: HomeAssistant) -> None:
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_COMMAND,
            {"id": 7, "cmd": "value"},
            blocking=True,
        )


async def test_send_command_returns_optional_response(hass: HomeAssistant) -> None:
    _, client = _loaded_entry(hass)
    async_setup_services(hass)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        {"id": 7, "cmd": "value"},
        blocking=True,
        return_response=True,
    )

    assert response == {"response": {"id": 7, "cmdres": 0}}
    client.async_send_command.assert_awaited_once_with(7, "value")


async def test_send_bulk_is_sequential(hass: HomeAssistant) -> None:
    _, client = _loaded_entry(hass)
    client.async_send_command = AsyncMock(
        side_effect=[{"id": 7, "cmdres": 0}, {"id": 8, "cmdres": 0}]
    )
    async_setup_services(hass)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_BULK,
        {
            "commands": [
                {"id": 7, "cmd": "first"},
                {"id": 8, "cmd": "second"},
            ]
        },
        blocking=True,
        return_response=True,
    )

    assert response == {
        "responses": [
            {"id": 7, "response": {"id": 7, "cmdres": 0}},
            {"id": 8, "response": {"id": 8, "cmdres": 0}},
        ]
    }
    assert client.async_send_command.await_args_list[0].args == (7, "first")
    assert client.async_send_command.await_args_list[1].args == (8, "second")


async def test_connection_error_is_translated(hass: HomeAssistant) -> None:
    _, client = _loaded_entry(hass)
    client.async_send_command = AsyncMock(side_effect=ZontConnectionError)
    async_setup_services(hass)

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_COMMAND,
            {"id": 7, "cmd": "value"},
            blocking=True,
        )

    assert raised.value.translation_key == "controller_offline"
