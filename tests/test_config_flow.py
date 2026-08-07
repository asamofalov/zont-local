"""Tests for the ZONT WebSocket config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.zont_ws.client import (
    ZontAuthenticationError,
    ZontConnectionError,
)
from custom_components.zont_ws.const import DOMAIN
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

USER_DATA = {
    CONF_URL: "ws://controller.local/ws",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
}


async def test_user_flow_success(hass: HomeAssistant, monkeypatch) -> None:
    validate = AsyncMock()
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_validate_connection", validate
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_DATA
    validate.assert_awaited_once()


async def test_user_flow_rejects_invalid_url(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={**USER_DATA, CONF_URL: "https://controller.local"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}


async def test_user_flow_reports_connection_error(
    hass: HomeAssistant, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_validate_connection",
        AsyncMock(side_effect=ZontConnectionError),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_reports_invalid_auth(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_validate_connection",
        AsyncMock(side_effect=ZontAuthenticationError),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_updates_url(hass: HomeAssistant, monkeypatch) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_DATA)
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_validate_connection", AsyncMock()
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data={CONF_URL: "wss://new-controller.local/ws"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert entry.data[CONF_URL] == "wss://new-controller.local/ws"


async def test_reauth_updates_credentials(hass: HomeAssistant, monkeypatch) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_DATA)
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_validate_connection", AsyncMock()
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_USERNAME: "new-user",
            CONF_PASSWORD: "new-password",
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert entry.data[CONF_USERNAME] == "new-user"
    assert entry.data[CONF_PASSWORD] == "new-password"
