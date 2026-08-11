"""Tests for ZONT controller identity helpers."""

from unittest.mock import AsyncMock

import pytest
from custom_components.zont_local.presentation import (
    controller_device_name,
    controller_entry_title,
)
from custom_components.zont_local.protocol import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
)
from custom_components.zont_local.protocol.controller import (
    COMMAND_RESTART,
    ZontCommunicationChannel,
    ZontControllerInfo,
    ZontEthernetStatus,
    ZontGsmRegistrationState,
    ZontGsmStatus,
    ZontIdentificationError,
    ZontPowerSource,
    ZontPowerStatus,
    ZontServerStatus,
    ZontWifiStatus,
    async_identify_controller,
    async_identify_controller_from_requests,
    async_restart_controller,
    controller_configuration_url,
    controller_endpoint,
    controller_websocket_url,
    parse_ethernet_status_response,
    parse_gsm_status_response,
    parse_identity_response,
    parse_power_status_response,
    parse_serial_response,
    parse_server_status_response,
    parse_supply_voltage_response,
    parse_wifi_status_response,
)


def test_parse_controller_responses() -> None:
    """Parse the fields confirmed by the local controller page."""
    serial_number = parse_serial_response("#S54:abcdef123456")
    assert serial_number == "ABCDEF123456"
    assert parse_identity_response("#S7:H1V02_PRO 700 625") == (
        "H1V02 PRO",
        "700",
        "625",
    )

    info = ZontControllerInfo(serial_number).with_identity_response(
        "#S7:H1V02_PRO 700 625"
    )
    assert info.as_dict() == {
        "serial_number": "ABCDEF123456",
        "model": "H1V02 PRO",
        "board_model": "700",
        "firmware_version": "625",
    }
    assert ZontControllerInfo.from_mapping(info.as_dict()) == info


def test_reject_invalid_controller_responses() -> None:
    """Reject malformed or ambiguous identity fields."""
    for response in ("#S54:short", "#S54:ABCDEF1234567", "#S7:ABCDEF123456"):
        try:
            parse_serial_response(response)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid serial response: {response}")

    for response in ("#S7:", "#S7:H1V02_PRO 700", "#S7:H1V02_PRO 700 625 extra"):
        try:
            parse_identity_response(response)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid identity response: {response}")


@pytest.mark.parametrize(
    ("response", "cloud_connected", "channels", "channel_state"),
    [
        ("#S224:0 0 0 0", False, frozenset(), "none"),
        (
            "#S224:1 1 0 0",
            True,
            frozenset({ZontCommunicationChannel.GSM}),
            "gsm",
        ),
        (
            "#S224:1 0 1 0",
            True,
            frozenset({ZontCommunicationChannel.WIFI}),
            "wifi",
        ),
        (
            "#S224:1 0 0 1",
            True,
            frozenset({ZontCommunicationChannel.ETHERNET}),
            "ethernet",
        ),
        (
            "#S224:1 1 1 0",
            True,
            frozenset({ZontCommunicationChannel.GSM, ZontCommunicationChannel.WIFI}),
            "gsm_wifi",
        ),
        (
            "#S224:1 1 0 1",
            True,
            frozenset(
                {ZontCommunicationChannel.GSM, ZontCommunicationChannel.ETHERNET}
            ),
            "gsm_ethernet",
        ),
        (
            "#S224:1 0 1 1",
            True,
            frozenset(
                {ZontCommunicationChannel.WIFI, ZontCommunicationChannel.ETHERNET}
            ),
            "wifi_ethernet",
        ),
        (
            "#S224:1 1 1 1",
            True,
            frozenset(ZontCommunicationChannel),
            "gsm_wifi_ethernet",
        ),
    ],
)
def test_parse_server_status_response(
    response: str,
    cloud_connected: bool,
    channels: frozenset[ZontCommunicationChannel],
    channel_state: str,
) -> None:
    """Parse every supported communication-channel combination."""
    assert parse_server_status_response(response) == ZontServerStatus(
        cloud_connected=cloud_connected,
        channels=channels,
    )
    assert parse_server_status_response(response).channel_state == channel_state


@pytest.mark.parametrize(
    "response",
    ["#S224:!", "#S224:1 0 1", "#S224:1 0 1 2", "#S6:1 0 1 0"],
)
def test_reject_invalid_server_status(response: str) -> None:
    """Reject incomplete and ambiguous server-status responses."""
    with pytest.raises(ValueError):
        parse_server_status_response(response)


def test_parse_supply_voltage_response() -> None:
    """Convert controller decivolts while preserving the legacy helper."""
    assert parse_supply_voltage_response("#S6:123 0") == 12.3
    assert parse_supply_voltage_response("#S6:240 reserved") == 24.0


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("#S6:122 0", ZontPowerStatus(12.2, ZontPowerSource.MAIN)),
        ("#S6:120 1", ZontPowerStatus(12.0, ZontPowerSource.BATTERY)),
        ("#S6:240 reserved", ZontPowerStatus(24.0, None)),
    ],
)
def test_parse_power_status_response(
    response: str,
    expected: ZontPowerStatus,
) -> None:
    """Parse supply voltage without guessing an unknown source code."""
    assert parse_power_status_response(response) == expected


def test_parse_wifi_status_without_retaining_network_identifiers() -> None:
    """Parse Wi-Fi connection and normalize the observed RSSI magnitude."""
    status = parse_wifi_status_response(
        "#S198:8 86 02:00:00:00:00:01 192.0.2.10 255.255.255.0 192.0.2.1"
    )

    assert status == ZontWifiStatus(connected=True, raw_signal=86)
    assert status.signal_percent == 28
    assert (
        parse_wifi_status_response(
            "#S198:0 0 02:00:00:00:00:01 0.0.0.0 0.0.0.0 0.0.0.0"
        ).signal_percent
        == 0
    )
    assert ZontWifiStatus(connected=True, raw_signal=50).signal_percent == 100
    assert ZontWifiStatus(connected=True, raw_signal=100).signal_percent == 1


def test_parse_ethernet_status_without_retaining_network_identifiers() -> None:
    """Parse only the confirmed Ethernet connection flag."""
    assert parse_ethernet_status_response(
        "#S205:02:00:00:00:00:01 192.0.2.10 255.255.255.0 192.0.2.1 1"
    ) == ZontEthernetStatus(connected=True)
    assert parse_ethernet_status_response(
        "#S205:02:00:00:00:00:01 192.0.2.10 255.255.255.0 192.0.2.1 0"
    ) == ZontEthernetStatus(connected=False)


@pytest.mark.parametrize(
    ("state", "expected"),
    list(enumerate(ZontGsmRegistrationState)),
)
def test_parse_gsm_registration_states(
    state: int,
    expected: ZontGsmRegistrationState,
) -> None:
    """Parse every confirmed GSM registration state."""
    status = parse_gsm_status_response(f"#S4:19 {state} Test Operator")

    assert status == ZontGsmStatus(expected, 19)
    assert status.signal_percent == 61


def test_unsupported_gsm_signal_does_not_hide_registration() -> None:
    """Keep registration available when the signal level is outside 0..31."""
    status = parse_gsm_status_response("#S4:99 4 Test Operator")

    assert status.registration is ZontGsmRegistrationState.UNKNOWN
    assert status.raw_signal is None
    assert status.signal_percent is None


@pytest.mark.parametrize(
    ("parser", "response"),
    [
        (parse_wifi_status_response, "#S198:!"),
        (parse_wifi_status_response, "#S198:8 bad mac ip mask gateway"),
        (parse_ethernet_status_response, "#S205:!"),
        (parse_ethernet_status_response, "#S205:mac ip mask gateway 2"),
        (parse_gsm_status_response, "#S4:0 6 Test Operator"),
        (parse_gsm_status_response, "#S4:bad 1 Test Operator"),
    ],
)
def test_reject_invalid_extended_status_response(parser, response: str) -> None:
    """Reject unsupported and malformed extended status data."""
    with pytest.raises(ValueError):
        parser(response)


@pytest.mark.parametrize(
    "response",
    ["#S6:!", "#S6:123", "#S6:12.3 0", "#S6:-10 0", "#S7:123 0"],
)
def test_reject_invalid_supply_voltage(response: str) -> None:
    """Reject supply-voltage responses outside the observed shape."""
    with pytest.raises(ValueError):
        parse_supply_voltage_response(response)


@pytest.mark.asyncio
async def test_restart_controller_does_not_wait_for_response() -> None:
    """Send the empirically confirmed restart command through the client."""
    client = AsyncMock()

    await async_restart_controller(client)

    client.async_send_system_command_without_response.assert_awaited_once_with(
        COMMAND_RESTART
    )


def test_controller_names_and_urls() -> None:
    """Build stable user-facing controller descriptions."""
    info = ZontControllerInfo(
        serial_number="ABCDEF123456",
        model="H1V02 PRO",
        board_model="700",
        firmware_version="625",
    )
    assert controller_entry_title(info, "192.0.2.10") == ("ZONT H1V02 PRO (192.0.2.10)")
    assert controller_entry_title(None, "2001:db8::1") == (
        "Контроллер ZONT ([2001:db8::1])"
    )
    assert controller_device_name(info) == "ZONT H1V02 PRO"
    assert controller_endpoint("2001:db8::1") == "[2001:db8::1]"
    assert controller_websocket_url("192.0.2.10") == "ws://192.0.2.10/ws"
    assert controller_websocket_url("2001:db8::1") == "ws://[2001:db8::1]/ws"
    assert controller_configuration_url("192.0.2.10") == "http://192.0.2.10"
    assert controller_configuration_url("2001:db8::1") == "http://[2001:db8::1]"


@pytest.mark.asyncio
async def test_identify_controller_uses_required_system_commands(monkeypatch) -> None:
    request = AsyncMock(
        return_value=[
            "#S54:abcdef123456",
            "#S7:H1V02_PRO 700 625",
        ]
    )
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.controller.async_request_system_commands",
        request,
    )
    credentials = ZontCredentials("user", "password")
    session = object()

    info = await async_identify_controller(  # type: ignore[arg-type]
        session,
        "ws://192.0.2.10/ws",
        credentials,
    )

    assert info == ZontControllerInfo(
        serial_number="ABCDEF123456",
        model="H1V02 PRO",
        board_model="700",
        firmware_version="625",
    )
    request.assert_awaited_once_with(
        session,
        "ws://192.0.2.10/ws",
        credentials,
        ("#S54?", "#S7?"),
        response_timeout=3.0,
    )


@pytest.mark.asyncio
async def test_identify_controller_reuses_authenticated_request_session() -> None:
    requests = AsyncMock()
    requests.async_send_system_command.side_effect = [
        "#S54:abcdef123456",
        "#S7:H1V02_PRO 700 625",
    ]

    info = await async_identify_controller_from_requests(requests)

    assert info.serial_number == "ABCDEF123456"
    assert [
        call.args for call in requests.async_send_system_command.await_args_list
    ] == [
        ("#S54?", 3.0),
        ("#S7?", 3.0),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses",
    [
        ["#S54:invalid", "#S7:H1V02_PRO 700 625"],
        ["#S54:ABCDEF123456", "#S7:H1V02_PRO 700"],
    ],
)
async def test_identify_controller_requires_both_valid_responses(
    monkeypatch,
    responses: list[str],
) -> None:
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.controller.async_request_system_commands",
        AsyncMock(return_value=responses),
    )

    with pytest.raises(ZontIdentificationError):
        await async_identify_controller(  # type: ignore[arg-type]
            object(),
            "ws://192.0.2.10/ws",
            ZontCredentials("user", "password"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_error",
    [ZontAuthenticationError(), ZontConnectionError()],
)
async def test_identify_controller_preserves_connection_errors(
    monkeypatch,
    client_error: Exception,
) -> None:
    monkeypatch.setattr(
        "custom_components.zont_local.protocol.controller.async_request_system_commands",
        AsyncMock(side_effect=client_error),
    )

    with pytest.raises(type(client_error)):
        await async_identify_controller(  # type: ignore[arg-type]
            object(),
            "ws://192.0.2.10/ws",
            ZontCredentials("user", "password"),
        )
