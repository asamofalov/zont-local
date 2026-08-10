"""Tests for the ZONT WebSocket config flow."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from custom_components.zont_ws import config_flow as zont_config_flow
from custom_components.zont_ws.const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONF_DHW_ON_TEMPERATURE,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_HEATING_OFF_MODE_ID,
    CONF_IMPORTED_OBJECT_IDS,
    DEFAULT_SCAN_INTERVAL,
    DHW_DEFAULT_ON_TEMPERATURE,
    DOMAIN,
)
from custom_components.zont_ws.protocol import (
    ZontAuthenticationError,
    ZontClient,
    ZontConnectionError,
    ZontCredentials,
)
from custom_components.zont_ws.protocol.controller import (
    ZontControllerInfo,
    ZontIdentificationError,
)
from custom_components.zont_ws.protocol.heating_config import (
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
)
from custom_components.zont_ws.protocol.heating_modes import ZontHeatingModeDiscovery
from custom_components.zont_ws.protocol.objects import (
    ZontHeatingCircuitData,
)
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
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
OFF_MODE_ID = 20504
OFF_MODE_DISCOVERY = ZontHeatingModeDiscovery(
    circuits={
        8362: ZontHeatingCircuitData(
            object_id=8362,
            object_type=16,
            name="ГВС",
            subtype=1,
        ),
        20496: ZontHeatingCircuitData(
            object_id=20496,
            object_type=16,
            name="Радиаторы",
            subtype=3,
        ),
    },
    states={
        8362: ZontHeatingCircuitInternalState(8362, 4097, 0, (OFF_MODE_ID,)),
        20496: ZontHeatingCircuitInternalState(20496, 4104, 0, (OFF_MODE_ID,)),
    },
    modes={
        OFF_MODE_ID: ZontHeatingModeConfiguration(
            OFF_MODE_ID,
            "Выключен",
            {8362: 0, 20496: 0},
        )
    },
)


def _mock_initial_discovery(monkeypatch, discovery=OFF_MODE_DISCOVERY) -> AsyncMock:
    mock = AsyncMock(
        return_value=(CONTROLLER_INFO, discovery, dict(discovery.circuits))
    )
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow."
        "_async_identify_and_discover_configuration",
        mock,
    )
    return mock


async def test_initial_discovery_uses_one_authenticated_connection(
    hass: HomeAssistant, monkeypatch
) -> None:
    requests = object()
    opened: list[tuple[str, ZontCredentials]] = []

    @asynccontextmanager
    async def open_session(session, url, credentials):
        opened.append((url, credentials))
        yield requests

    identify = AsyncMock(return_value=CONTROLLER_INFO)
    discover = AsyncMock(return_value=OFF_MODE_DISCOVERY)
    discover_objects = AsyncMock(return_value=OFF_MODE_DISCOVERY.circuits)
    monkeypatch.setattr(
        zont_config_flow, "async_open_temporary_request_session", open_session
    )
    monkeypatch.setattr(
        zont_config_flow, "async_identify_controller_from_requests", identify
    )
    monkeypatch.setattr(
        zont_config_flow, "async_discover_heating_modes_from_requests", discover
    )
    monkeypatch.setattr(
        zont_config_flow,
        "async_discover_objects_from_requests",
        discover_objects,
    )

    (
        info,
        discovery,
        objects,
    ) = await zont_config_flow._async_identify_and_discover_configuration(
        hass, USER_DATA
    )

    assert info == CONTROLLER_INFO
    assert discovery == OFF_MODE_DISCOVERY
    assert objects == OFF_MODE_DISCOVERY.circuits
    assert opened == [
        (
            "ws://192.0.2.10/ws",
            ZontCredentials("user", "password"),
        )
    ]
    identify.assert_awaited_once_with(requests)
    discover.assert_awaited_once_with(requests)
    discover_objects.assert_awaited_once_with(requests)


async def test_user_flow_success(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(ZontClient, "async_connect", AsyncMock())
    discover = _mock_initial_discovery(monkeypatch)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "heating_mode"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HEATING_OFF_MODE_ID: str(OFF_MODE_ID)},
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "devices"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_IMPORTED_OBJECT_IDS: ["8362", "20496"],
            CONF_AUTO_IMPORT_NEW_OBJECTS: True,
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == AUTO_TITLE
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == SERIAL_NUMBER
    assert result["options"] == {
        CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID,
        CONF_DHW_ON_TEMPERATURE: DHW_DEFAULT_ON_TEMPERATURE,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        CONF_IMPORTED_OBJECT_IDS: [8362, 20496],
        CONF_EXCLUDED_OBJECT_IDS: [],
        CONF_AUTO_IMPORT_NEW_OBJECTS: True,
    }
    discover.assert_awaited_once()
    assert discover.await_args.args[1] == USER_DATA


async def test_user_flow_rejects_duplicate_controller(
    hass: HomeAssistant, monkeypatch
) -> None:
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    existing.add_to_hass(hass)
    _mock_initial_discovery(monkeypatch)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_user_flow_requires_an_all_off_mode(
    hass: HomeAssistant, monkeypatch
) -> None:
    _mock_initial_discovery(
        monkeypatch,
        ZontHeatingModeDiscovery(
            circuits=OFF_MODE_DISCOVERY.circuits,
            states=OFF_MODE_DISCOVERY.states,
            modes={
                20501: ZontHeatingModeConfiguration(
                    20501,
                    "Комфорт",
                    {8362: 3330, 20496: 3140},
                )
            },
        ),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_off_mode"


async def test_initial_flow_allows_empty_device_selection(
    hass: HomeAssistant, monkeypatch
) -> None:
    monkeypatch.setattr(ZontClient, "async_connect", AsyncMock())
    _mock_initial_discovery(monkeypatch)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HEATING_OFF_MODE_ID: str(OFF_MODE_ID)},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_IMPORTED_OBJECT_IDS: [],
            CONF_AUTO_IMPORT_NEW_OBJECTS: False,
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_IMPORTED_OBJECT_IDS] == []
    assert result["options"][CONF_EXCLUDED_OBJECT_IDS] == [8362, 20496]
    assert result["options"][CONF_AUTO_IMPORT_NEW_OBJECTS] is False


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
    monkeypatch.setattr(ZontClient, "async_connect", AsyncMock())
    discover = _mock_initial_discovery(monkeypatch)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={**USER_DATA, CONF_HOST: "2001:0DB8:0:0::1"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HEATING_OFF_MODE_ID: str(OFF_MODE_ID)},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_IMPORTED_OBJECT_IDS: ["8362", "20496"],
            CONF_AUTO_IMPORT_NEW_OBJECTS: True,
        },
    )
    assert result["data"][CONF_HOST] == "2001:db8::1"
    assert result["title"] == "ZONT H1V02 PRO ([2001:db8::1])"
    assert discover.await_args.args[1][CONF_HOST] == "2001:db8::1"


async def test_user_flow_reports_connection_error(
    hass: HomeAssistant, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow."
        "_async_identify_and_discover_configuration",
        AsyncMock(side_effect=ZontConnectionError),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=USER_DATA
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_reports_invalid_auth(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.zont_ws.config_flow."
        "_async_identify_and_discover_configuration",
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
        "custom_components.zont_ws.config_flow."
        "_async_identify_and_discover_configuration",
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
