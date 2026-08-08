"""Tests for the ZONT WebSocket config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from custom_components.zont_ws.client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontWsClient,
)
from custom_components.zont_ws.const import CONF_AUTO_TITLE, CONF_CONTROLLER, DOMAIN
from custom_components.zont_ws.controller import (
    ZontControllerInfo,
    ZontIdentificationError,
)
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

USER_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
}
SERIAL_NUMBER = "ABCDEF123456"
CONTROLLER_INFO = ZontControllerInfo(
    serial_number=SERIAL_NUMBER,
    model="H1V02 PRO",
    board_model="700",
    firmware_version="625",
)
AUTO_TITLE = "ZONT H1V02 PRO (192.0.2.10)"
ENTRY_DATA = {
    **USER_DATA,
    CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
    CONF_AUTO_TITLE: AUTO_TITLE,
}


async def test_user_flow_success(hass: HomeAssistant, monkeypatch) -> None:
    identify = AsyncMock(return_value=CONTROLLER_INFO)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller", identify
    )
    monkeypatch.setattr(ZontWsClient, "async_start", AsyncMock())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == AUTO_TITLE
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == SERIAL_NUMBER
    identify.assert_awaited_once()
    assert identify.await_args.args[1] == "ws://192.0.2.10/ws"


async def test_user_flow_rejects_duplicate_controller(
    hass: HomeAssistant, monkeypatch
) -> None:
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    existing.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        AsyncMock(return_value=CONTROLLER_INFO),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.parametrize(
    "host",
    ["ws://192.0.2.10/ws", "controller.local"],
)
async def test_user_flow_rejects_invalid_host(
    hass: HomeAssistant,
    host: str,
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={**USER_DATA, CONF_HOST: host},
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_user_flow_normalizes_ipv6(hass: HomeAssistant, monkeypatch) -> None:
    identify = AsyncMock(return_value=CONTROLLER_INFO)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller", identify
    )
    monkeypatch.setattr(ZontWsClient, "async_start", AsyncMock())

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={**USER_DATA, CONF_HOST: "2001:0DB8:0:0::1"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "2001:db8::1"
    assert result["title"] == "ZONT H1V02 PRO ([2001:db8::1])"
    assert identify.await_args.args[1] == "ws://[2001:db8::1]/ws"


async def test_user_flow_reports_connection_error(
    hass: HomeAssistant, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        AsyncMock(side_effect=ZontConnectionError),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_reports_invalid_auth(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        AsyncMock(side_effect=ZontAuthenticationError),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_requires_controller_identification(
    hass: HomeAssistant, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        AsyncMock(side_effect=ZontIdentificationError),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_identify"}


async def test_reconfigure_form_contains_all_connection_settings(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert {marker.schema for marker in result["data_schema"].schema} == {
        CONF_HOST,
        CONF_USERNAME,
        CONF_PASSWORD,
    }


async def test_reconfigure_updates_connection_settings(
    hass: HomeAssistant, monkeypatch
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    identify = AsyncMock(return_value=CONTROLLER_INFO)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        identify,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data={
            CONF_HOST: "192.0.2.11",
            CONF_USERNAME: "new-user",
            CONF_PASSWORD: "new-password",
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert entry.data[CONF_HOST] == "192.0.2.11"
    assert entry.data[CONF_USERNAME] == "new-user"
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.title == "ZONT H1V02 PRO (192.0.2.11)"
    assert identify.await_args.args[1] == "ws://192.0.2.11/ws"
    assert identify.await_args.args[2] == ZontCredentials("new-user", "new-password")


async def test_reconfigure_keeps_password_and_custom_title(
    hass: HomeAssistant, monkeypatch
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Котельная",
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    identify = AsyncMock(return_value=CONTROLLER_INFO)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        identify,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data={
            CONF_HOST: "192.0.2.11",
            CONF_USERNAME: "new-user",
            CONF_PASSWORD: "",
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert entry.data[CONF_PASSWORD] == USER_DATA[CONF_PASSWORD]
    assert entry.title == "Котельная"
    assert identify.await_args.args[2] == ZontCredentials(
        "new-user", USER_DATA[CONF_PASSWORD]
    )


async def test_reconfigure_rejects_invalid_credentials(
    hass: HomeAssistant, monkeypatch
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        AsyncMock(side_effect=ZontAuthenticationError),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data={
            CONF_HOST: "192.0.2.11",
            CONF_USERNAME: "new-user",
            CONF_PASSWORD: "wrong-password",
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data == ENTRY_DATA


@pytest.mark.parametrize(
    ("user_input", "expected_error"),
    [
        (
            {
                CONF_HOST: "controller.local",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "",
            },
            "invalid_host",
        ),
        (
            {
                CONF_HOST: "192.0.2.11",
                CONF_USERNAME: "",
                CONF_PASSWORD: "",
            },
            "invalid_auth",
        ),
    ],
)
async def test_reconfigure_rejects_invalid_connection_settings(
    hass: HomeAssistant,
    monkeypatch,
    user_input: dict[str, str],
    expected_error: str,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    identify = AsyncMock(return_value=CONTROLLER_INFO)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        identify,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data=user_input,
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    assert entry.data == ENTRY_DATA
    identify.assert_not_awaited()


async def test_reconfigure_rejects_different_controller(
    hass: HomeAssistant, monkeypatch
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        AsyncMock(
            return_value=ZontControllerInfo(
                serial_number="123456ABCDEF",
                model="H1V02 PRO",
                board_model="700",
                firmware_version="625",
            )
        ),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data={
            CONF_HOST: "192.0.2.11",
            CONF_USERNAME: USER_DATA[CONF_USERNAME],
            CONF_PASSWORD: "",
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "different_controller"}
    assert entry.data[CONF_HOST] == USER_DATA[CONF_HOST]


async def test_reauth_updates_credentials(hass: HomeAssistant, monkeypatch) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Котельная",
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow.async_identify_controller",
        AsyncMock(return_value=CONTROLLER_INFO),
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
    assert entry.title == "Котельная"
