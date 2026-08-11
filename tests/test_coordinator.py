"""Tests for the shared ZONT data coordinator."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from custom_components.zont_local.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from custom_components.zont_local.coordinator import (
    ZontDataUpdateCoordinator,
)
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.protocol import (
    ZontClient,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from custom_components.zont_local.protocol.controller import (
    COMMAND_ETHERNET_INFO,
    COMMAND_GSM_INFO,
    COMMAND_SERVER_INFO,
    COMMAND_SUPPLY_VOLTAGE,
    COMMAND_WIFI_INFO,
    ZontCommunicationChannel,
    ZontControllerInfo,
    ZontGsmRegistrationState,
    ZontPowerSource,
)
from custom_components.zont_local.protocol.heating_config import (
    ZontConsumerControlMode,
    ZontHeatingCircuitInternalState,
    immutable_heating_states,
)
from custom_components.zont_local.protocol.mixer import (
    ZontMixerInternalState,
    immutable_mixer_states,
)
from custom_components.zont_local.protocol.objects import (
    OBJECT_TYPE_RELAY,
    OBJECT_TYPE_SECURITY_ZONE,
    OBJECT_TYPE_USER_ELEMENT,
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontHeatingCircuitMode,
    ZontMixerData,
    ZontMixerDirection,
    ZontNtcTemperatureSensorData,
    ZontPumpData,
    ZontRadioSensorData,
    ZontRelayData,
    ZontSecurityZoneData,
    ZontUserElementData,
    immutable_objects,
)
from custom_components.zont_local.protocol.relay import (
    ZontRelayConfiguration,
    ZontRelayInternalState,
    immutable_relay_configurations,
    immutable_relay_states,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


class _ObjectIdsMock(AsyncMock):
    """Keep legacy discovery fixtures focused on their original object types."""

    relay_ids: list[int]
    security_zone_ids: list[int]
    user_element_ids: list[int]

    def __init__(self) -> None:
        """Initialize an object-ID mock with no relays."""
        super().__init__(return_value=[])
        self.relay_ids = []
        self.security_zone_ids = []
        self.user_element_ids = []

    async def _execute_mock_call(self, *args: object, **kwargs: object) -> object:
        special_ids = {
            OBJECT_TYPE_RELAY: self.relay_ids,
            OBJECT_TYPE_SECURITY_ZONE: self.security_zone_ids,
            OBJECT_TYPE_USER_ELEMENT: self.user_element_ids,
        }
        if args and args[0] in special_ids:
            side_effect = self.side_effect
            return_value = self.return_value
            self.side_effect = None
            self.return_value = list(special_ids[int(args[0])])
            try:
                return await super()._execute_mock_call(*args, **kwargs)
            finally:
                self.side_effect = side_effect
                self.return_value = return_value
        return await super()._execute_mock_call(*args, **kwargs)


class _SystemCommandMock(AsyncMock):
    """Keep existing coordinator fixtures focused on their original sources."""

    optional_responses: dict[str, str]

    def __init__(self) -> None:
        """Initialize optional controller sources as unsupported."""
        super().__init__()
        self.optional_responses = {
            COMMAND_WIFI_INFO: "#S198:!",
            COMMAND_ETHERNET_INFO: "#S205:!",
            COMMAND_GSM_INFO: "#S4:!",
        }

    async def _execute_mock_call(self, *args: object, **kwargs: object) -> object:
        if args and args[0] in self.optional_responses:
            side_effect = self.side_effect
            return_value = self.return_value
            self.side_effect = None
            self.return_value = self.optional_responses[str(args[0])]
            try:
                return await super()._execute_mock_call(*args, **kwargs)
            finally:
                self.side_effect = side_effect
                self.return_value = return_value
        return await super()._execute_mock_call(*args, **kwargs)


_OPTIONAL_STATUS_CALLS = [
    call(COMMAND_WIFI_INFO, response_timeout=5.0),
    call(COMMAND_ETHERNET_INFO, response_timeout=5.0),
    call(COMMAND_GSM_INFO, response_timeout=5.0),
]


def _coordinator(
    hass: HomeAssistant,
    scan_interval: object | None = None,
    initial_info: ZontControllerInfo | None = None,
    on_controller_info: MagicMock | None = None,
) -> tuple[ZontDataUpdateCoordinator, MagicMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
        options=(
            {CONF_SCAN_INTERVAL: scan_interval} if scan_interval is not None else {}
        ),
    )
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_send_system_command = _SystemCommandMock()
    client.async_get_object_ids = _ObjectIdsMock()
    client.async_get_object_state = AsyncMock()
    coordinator = ZontDataUpdateCoordinator(
        hass,
        entry,
        client,
        initial_info=initial_info,
        on_controller_info=on_controller_info or MagicMock(),
    )
    return coordinator, client


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, DEFAULT_SCAN_INTERVAL),
        (MIN_SCAN_INTERVAL, MIN_SCAN_INTERVAL),
        (MAX_SCAN_INTERVAL, MAX_SCAN_INTERVAL),
        (60.0, 60),
        (MIN_SCAN_INTERVAL - 1, DEFAULT_SCAN_INTERVAL),
        (MAX_SCAN_INTERVAL + 1, DEFAULT_SCAN_INTERVAL),
        (10.5, DEFAULT_SCAN_INTERVAL),
        (True, DEFAULT_SCAN_INTERVAL),
        ("60", DEFAULT_SCAN_INTERVAL),
    ],
)
def test_coordinator_uses_safe_scan_interval(
    hass: HomeAssistant,
    configured: object | None,
    expected: int,
) -> None:
    coordinator, _ = _coordinator(hass, configured)

    assert coordinator.update_interval == timedelta(seconds=expected)


def test_coordinator_applies_changed_scan_interval(hass: HomeAssistant) -> None:
    """A changed polling option takes effect without recreating the coordinator."""
    coordinator, _ = _coordinator(hass, DEFAULT_SCAN_INTERVAL)
    coordinator._entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        coordinator._entry,
        options={CONF_SCAN_INTERVAL: MIN_SCAN_INTERVAL},
    )

    coordinator.async_apply_options()

    assert coordinator.update_interval == timedelta(seconds=MIN_SCAN_INTERVAL)


async def test_shutdown_cancels_active_poll_before_client_stop(
    hass: HomeAssistant,
) -> None:
    """Unload must not fail pending protocol requests by stopping the client first."""
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [[1001]]
    request_started = asyncio.Event()

    async def wait_for_shutdown(_object_id: int) -> dict[str, object]:
        request_started.set()
        await asyncio.Future()
        return {}

    client.async_get_object_state.side_effect = wait_for_shutdown
    refresh_task = hass.async_create_task(coordinator.async_refresh())
    await request_started.wait()

    await coordinator.async_shutdown()

    assert refresh_task.done()
    with pytest.raises(asyncio.CancelledError):
        await refresh_task


async def test_refresh_builds_one_controller_snapshot(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 1 1 0",
        "#S6:123 0",
    ]

    await coordinator.async_refresh()

    status = coordinator.data.controller
    assert coordinator.last_update_success
    assert status.server_status is not None
    assert status.server_status.cloud_connected
    assert status.server_status.channels == {
        ZontCommunicationChannel.GSM,
        ZontCommunicationChannel.WIFI,
    }
    assert status.server_status.channel_state == "gsm_wifi"
    assert status.supply_voltage == 12.3
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=5.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=5.0),
        *_OPTIONAL_STATUS_CALLS,
    ]
    assert client.async_get_object_ids.await_args_list == [
        call(0),
        call(1),
        call(2),
        call(6),
        call(8),
        call(10),
        call(16),
        call(17),
        call(27),
        call(15),
        call(14),
    ]


async def test_unavailable_source_is_retried_during_next_update(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:!",
        "#S6:123 0",
        "#S224:1 0 1 0",
        "#S6:124 0",
    ]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.controller.server_status is not None
    assert coordinator.data.controller.server_status.cloud_connected
    assert coordinator.data.controller.supply_voltage == 12.4
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=5.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=5.0),
        *_OPTIONAL_STATUS_CALLS,
        call(COMMAND_SERVER_INFO, response_timeout=5.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=5.0),
        *_OPTIONAL_STATUS_CALLS,
    ]


async def test_malformed_source_is_retried_during_next_update(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:not-a-status",
        "#S6:123 0",
        "#S224:1 0 1 0",
        "#S6:124 0",
    ]

    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.controller.server_status is None

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.controller.server_status is not None
    assert coordinator.data.controller.server_status.cloud_connected
    commands = [
        awaited.args[0] for awaited in client.async_send_system_command.await_args_list
    ]
    assert commands.count(COMMAND_SERVER_INFO) == 2


async def test_timed_out_source_is_retried_after_failed_update(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        ZontRequestTimeoutError("temporary timeout"),
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]

    await coordinator.async_refresh()
    assert not coordinator.last_update_success

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.controller.server_status is not None
    assert coordinator.data.controller.supply_voltage == 12.3
    commands = [
        awaited.args[0] for awaited in client.async_send_system_command.await_args_list
    ]
    assert commands.count(COMMAND_SERVER_INFO) == 2


async def test_controller_information_timeout_is_retried(
    hass: HomeAssistant,
) -> None:
    old_info = ZontControllerInfo(
        serial_number="ABCDEF123456",
        model="H1V02 PRO",
        board_model="700",
        firmware_version="624",
    )
    on_controller_info = MagicMock()
    coordinator, client = _coordinator(
        hass,
        initial_info=old_info,
        on_controller_info=on_controller_info,
    )
    client.async_send_system_command.side_effect = [
        ZontRequestTimeoutError("temporary timeout"),
        "#S7:H1V02_PRO 700 625",
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data.controller.info == old_info

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.controller.info is not None
    assert coordinator.data.controller.info.firmware_version == "625"
    on_controller_info.assert_called_once_with(coordinator.data.controller.info)


async def test_refresh_builds_extended_controller_status(hass: HomeAssistant) -> None:
    """Build safe Wi-Fi, GSM and power status while skipping Ethernet support."""
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:122 0",
        "#S224:1 0 1 0",
        "#S6:122 0",
    ]
    client.async_send_system_command.optional_responses.update(
        {
            COMMAND_WIFI_INFO: (
                "#S198:8 86 02:00:00:00:00:01 192.0.2.10 255.255.255.0 192.0.2.1"
            ),
            COMMAND_GSM_INFO: "#S4:0 2 Test Operator",
        }
    )

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    status = coordinator.data.controller
    assert status.supply_voltage == 12.2
    assert status.power_source is ZontPowerSource.MAIN
    assert status.wifi_status is not None
    assert status.wifi_status.connected
    assert status.wifi_status.signal_percent == 28
    assert status.ethernet_status is None
    assert status.gsm_status is not None
    assert status.gsm_status.registration is ZontGsmRegistrationState.SEARCHING
    assert status.gsm_status.signal_percent == 0
    commands = [
        awaited.args[0] for awaited in client.async_send_system_command.await_args_list
    ]
    assert commands.count(COMMAND_WIFI_INFO) == 2
    assert commands.count(COMMAND_ETHERNET_INFO) == 2
    assert commands.count(COMMAND_GSM_INFO) == 2


async def test_disconnected_client_marks_snapshot_unavailable(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.is_connected = False

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    client.async_send_system_command.assert_not_awaited()


async def test_unchanged_snapshot_does_not_notify_listeners(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    listener = MagicMock()
    unsubscribe = coordinator.async_add_listener(listener)

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    listener.assert_called_once()
    unsubscribe()
    await coordinator.async_shutdown()


async def test_refresh_discovers_digital_bus_adapter(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [[], [], [4097], [], [], [], [], []]
    client.async_get_object_state.return_value = {
        "id": 4097,
        "type": 6,
        "name": "Navien",
        "water": 35,
        "dhw": 29,
        "return": 30.4,
        "modul": 99,
        "press": 2.4,
        "state": 1,
        "err": 0,
    }

    await coordinator.async_refresh()

    adapter = coordinator.data.objects[4097]
    assert isinstance(adapter, ZontDigitalBusAdapterData)
    assert adapter.available
    assert adapter.flow_temperature == 35.0
    assert adapter.return_temperature == 30.4
    assert adapter.state is ZontDigitalBusState.RUNNING
    client.async_get_object_state.assert_awaited_once_with(4097)


async def test_refresh_discovers_security_zone(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.security_zone_ids = [10657]
    client.async_get_object_state.return_value = {
        "id": 10657,
        "type": 2,
        "name": "Тестовая зона",
        "s": 1,
        "trig": 1,
    }

    await coordinator.async_refresh()

    zone = coordinator.data.objects[10657]
    assert isinstance(zone, ZontSecurityZoneData)
    assert zone.armed is True
    assert zone.triggered is True
    assert zone.available


async def test_refresh_discovers_user_element(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.user_element_ids = [10691]
    client.async_get_object_state.return_value = {
        "id": 10691,
        "type": 10,
        "stype": 2,
        "name": "Элемент управления 2",
        "s": 1,
        "t": "Включено",
    }

    await coordinator.async_refresh()

    element = coordinator.data.objects[10691]
    assert isinstance(element, ZontUserElementData)
    assert element.raw_state == 1
    assert element.text == "Включено"
    assert element.available


def test_push_updates_user_element_state_and_text(hass: HomeAssistant) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                10691: ZontUserElementData(
                    10691,
                    10,
                    "Режим",
                    subtype=2,
                    raw_state=0,
                    text="Выключено",
                )
            }
        ),
    )

    coordinator._async_message_received(
        {
            "id": 10691,
            "type": 10,
            "stype": 2,
            "name": "Режим",
            "s": 1,
            "t": "Включено",
        }
    )

    element = coordinator.data.objects[10691]
    assert isinstance(element, ZontUserElementData)
    assert element.raw_state == 1
    assert element.text == "Включено"


async def test_failed_object_becomes_unavailable_without_losing_values(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [4097],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [4097],
        [],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.side_effect = [
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": 35,
            "state": 0,
            "err": 0,
        },
        {"id": 4097, "req_state": 0, "failed": 1},
    ]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    adapter = coordinator.data.objects[4097]
    assert not adapter.available
    assert adapter.flow_temperature == 35.0
    assert coordinator.last_update_success


async def test_object_protocol_error_is_isolated_and_retried(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {4097: ZontDigitalBusAdapterData(4097, 6, "Navien", True, 35.0)}
        ),
    )
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        ZontProtocolError,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [4097],
        [],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 4097,
        "type": 6,
        "name": "Navien",
        "water": 36,
    }

    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert not coordinator.data.objects[4097].available

    await coordinator.async_refresh()
    assert coordinator.data.objects[4097].available
    assert coordinator.data.objects[4097].flow_temperature == 36.0


async def test_refresh_discovers_temperature_sensor_and_adapter(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [8196],
        [4097],
        [],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.side_effect = [
        {
            "id": 8196,
            "type": 1,
            "name": "Погода из интернета",
            "t": 19.7,
            "a": 1,
            "trig": 0,
        },
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": 35,
        },
    ]

    await coordinator.async_refresh()

    sensor = coordinator.data.objects[8196]
    assert isinstance(sensor, ZontDigitalTemperatureSensorData)
    assert sensor.temperature == 19.7
    assert sensor.available
    assert isinstance(coordinator.data.objects[4097], ZontDigitalBusAdapterData)
    assert client.async_get_object_ids.await_args_list == [
        call(0),
        call(1),
        call(2),
        call(6),
        call(8),
        call(10),
        call(16),
        call(17),
        call(27),
        call(15),
        call(14),
    ]
    assert client.async_get_object_state.await_args_list == [
        call(8196),
        call(4097),
    ]


async def test_temperature_type_error_does_not_block_adapter_refresh(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                8196: ZontDigitalTemperatureSensorData(
                    object_id=8196,
                    object_type=1,
                    name="Погода из интернета",
                    temperature=19.7,
                )
            }
        ),
    )
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        ZontProtocolError,
        [4097],
        [],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 4097,
        "type": 6,
        "name": "Navien",
        "water": 35,
    }

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert not coordinator.data.objects[8196].available
    assert coordinator.data.objects[4097].available


async def test_refresh_discovers_analog_input_before_other_objects(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [20550],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 20550,
        "type": 0,
        "stype": 0,
        "name": "Контроль напряжения питания",
        "v": 12.2,
        "u": 0,
        "trig": 0,
        "a": 1,
    }

    await coordinator.async_refresh()

    analog_input = coordinator.data.objects[20550]
    assert isinstance(analog_input, ZontAnalogInputData)
    assert analog_input.value == 12.2
    assert analog_input.unit_code == 0
    assert analog_input.triggered is False
    assert analog_input.available
    assert client.async_get_object_ids.await_args_list == [
        call(0),
        call(1),
        call(2),
        call(6),
        call(8),
        call(10),
        call(16),
        call(17),
        call(27),
        call(15),
        call(14),
    ]


async def test_invalid_analog_input_does_not_block_other_object_types(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [20550],
        [],
        [4097],
        [],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.side_effect = [
        {
            "id": 20550,
            "type": 0,
            "stype": -1,
            "name": "Некорректный вход",
        },
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": 35,
        },
    ]

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert 20550 not in coordinator.data.objects
    assert coordinator.data.objects[4097].available


async def test_push_merges_partial_analog_state_and_availability(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                20550: ZontAnalogInputData(
                    object_id=20550,
                    object_type=0,
                    name="Контроль напряжения питания",
                    subtype=0,
                    value=12.2,
                    unit_code=0,
                    triggered=False,
                )
            }
        ),
    )

    coordinator._async_message_received({"id": 20550, "trig": 1})

    analog_input = coordinator.data.objects[20550]
    assert isinstance(analog_input, ZontAnalogInputData)
    assert analog_input.available
    assert analog_input.value == 12.2
    assert analog_input.unit_code == 0
    assert analog_input.triggered is True

    coordinator._async_message_received({"id": 20550, "a": 0})

    analog_input = coordinator.data.objects[20550]
    assert not analog_input.available
    assert analog_input.value == 12.2
    assert analog_input.triggered is True


async def test_refresh_discovers_radio_sensor(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [12001],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 12001,
        "type": 8,
        "stype": 18,
        "name": "Гостиная",
        "t": 23.4,
        "h": 48,
        "b": 2.91,
        "r": 86,
        "a": 1,
    }

    await coordinator.async_refresh()

    sensor = coordinator.data.objects[12001]
    assert isinstance(sensor, ZontRadioSensorData)
    assert sensor.temperature == 23.4
    assert sensor.humidity == 48
    assert sensor.battery_voltage == 2.91
    assert sensor.signal_strength_raw == 86
    assert sensor.available
    assert client.async_get_object_ids.await_args_list == [
        call(0),
        call(1),
        call(2),
        call(6),
        call(8),
        call(10),
        call(16),
        call(17),
        call(27),
        call(15),
        call(14),
    ]


async def test_radio_type_error_does_not_block_ntc_refresh(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        ZontProtocolError,
        [],
        [],
        [20487],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 20487,
        "type": 27,
        "name": "Температура котла",
        "t": 45.6,
        "a": 1,
    }

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert isinstance(coordinator.data.objects[20487], ZontNtcTemperatureSensorData)


async def test_refresh_discovers_heating_circuit(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Y8362$3330,3330,[],0,0,20501,4097,0,[20501,20504],0,0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [8362],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 8362,
        "type": 16,
        "stype": 1,
        "name": "ГВС",
        "c": 29,
        "s": 60,
        "m": "heat",
        "m_id": 20501,
        "f": 0,
    }

    await coordinator.async_refresh()

    circuit = coordinator.data.objects[8362]
    assert isinstance(circuit, ZontHeatingCircuitData)
    assert circuit.current_temperature == 29
    assert circuit.target_temperature == 60
    assert circuit.mode is ZontHeatingCircuitMode.HEAT
    assert circuit.available
    state = coordinator.data.heating_states[8362]
    assert state.calculated_water_temperature == 60
    assert state.is_heating is False
    assert state.applicable_mode_ids == (
        20501,
        20504,
    )


async def test_refresh_resolves_consumer_water_range(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z20496:16,'Радиаторы',3,3140,3530,0,0,4104,0,20,[9044],"
        "2562,0,0,2730,0,0,0,0,0,0,0,3230,100,10,0,0,0,0,0",
        "#Y20496$3160,3140,[],0,0,0,4104,138,[20504],0,0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [20496],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 20496,
        "type": 16,
        "stype": 3,
        "name": "Радиаторы",
        "c": 42.5,
        "s": 41,
        "m": "heat",
        "m_id": 0,
        "f": 0,
    }

    await coordinator.async_refresh()

    control = coordinator.data.heating_controls[20496]
    assert control.control_mode is ZontConsumerControlMode.WATER
    assert (control.min_temperature, control.max_temperature) == (41, 80)
    state = coordinator.data.heating_states[20496]
    assert state.calculated_water_temperature == 43
    assert state.is_heating is False
    assert state.is_blocked
    assert state.has_sensor_fault
    assert state.is_summer_mode
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=5.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=5.0),
        *_OPTIONAL_STATUS_CALLS,
        call("#Z20496?"),
        call("#Y20496?"),
    ]


async def test_refresh_discovers_heating_modes_and_dhw_applicability(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [8362, 20496],
        [],
        [],
        [],
        [20504],
    ]
    client.async_get_object_state.side_effect = [
        {
            "id": 8362,
            "type": 16,
            "stype": 1,
            "name": "ГВС",
            "c": 29,
            "s": 60,
            "m": "heat",
            "m_id": 20501,
        },
        {
            "id": 20496,
            "type": 16,
            "stype": 3,
            "name": "Радиаторы",
            "c": 42.5,
            "s": 41,
            "m": "heat",
            "m_id": 0,
        },
    ]
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z20504:20,'Выключен',[8362,20496],[0,0],[0,0],29,[0,0],10,0,10,4,0",
        "#Y8362$3330,3330,[],0,0,20501,4097,0,[20501,20504],0,0",
        "#Z20496:16,'Радиаторы',3,3140,3530,0,0,4104,0,20,[9044],"
        "2562,0,0,2730,0,0,0,0,0,0,0,3230,100,10,0,0,0,0,0",
        "#Y20496$3160,3140,[],0,0,0,4104,1,[20504],0,0",
    ]

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.heating_modes[20504].name == "Выключен"
    assert coordinator.data.heating_modes[20504].disables_circuit(8362)
    assert coordinator.data.heating_states[8362].applicable_mode_ids == (
        20501,
        20504,
    )


async def test_air_sensor_configuration_is_cached_but_state_is_refreshed(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z9825:16,'Кабинет',3,3080,3430,4110,0,4104,0,40,[9044],"
        "2049,0,0,2730,10139,0,0,2930,0,0,0,3230,100,10,0,0,0,0,0",
        "#Y9825$2930,2950,[],0,0,20501,4110,1,[20501,20504],0,0",
        "#Z4110:1,123,'Кабинет',3130,2830,5,300000,[],[],[],0",
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Y9825$2930,2960,[],0,0,20501,4110,1,[20501,20504],0,0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [9825],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [9825],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.side_effect = [
        {
            "id": 9825,
            "type": 16,
            "stype": 3,
            "name": "Кабинет",
            "c": 24.7,
            "s": 22,
            "m": "heat",
        },
        {
            "id": 9825,
            "type": 16,
            "stype": 3,
            "name": "Кабинет",
            "c": 24.8,
            "s": 23,
            "m": "heat",
        },
    ]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    control = coordinator.data.heating_controls[9825]
    assert control.control_mode is ZontConsumerControlMode.AIR_PID
    assert control.has_weather_compensation
    assert (control.min_temperature, control.max_temperature) == (10, 40)
    assert coordinator.data.heating_states[9825].status_register == 1
    commands = [
        call.args[0] for call in client.async_send_system_command.await_args_list
    ]
    assert commands.count("#Z9825?") == 1
    assert commands.count("#Z4110?") == 1
    assert commands.count("#Y9825?") == 2


async def test_failed_internal_state_read_clears_current_state_only(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z20496:16,'Радиаторы',3,3140,3530,0,0,4104,0,20,[9044],"
        "2562,0,0,2730,0,0,0,0,0,0,0,3230,100,10,0,0,0,0,0",
        "#Y20496$3160,3140,[],0,0,0,4104,2,[20504],0,0",
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Y20496:!",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [20496],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [20496],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 20496,
        "type": 16,
        "stype": 3,
        "name": "Радиаторы",
        "c": 42.5,
        "s": 41,
        "m": "heat",
        "f": 0,
    }

    await coordinator.async_refresh()
    assert coordinator.data.heating_states[20496].is_blocked

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert 20496 not in coordinator.data.heating_states
    assert coordinator.data.heating_controls[20496].can_set_temperature


async def test_invalid_consumer_configuration_does_not_block_other_circuit(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z9171:!",
        "#Y9171$3090,2980,[],0,0,20501,4103,1,[20501],0,0",
        "#Z20496:16,'Радиаторы',3,3140,3530,0,0,4104,0,20,[9044],"
        "2562,0,0,2730,0,0,0,0,0,0,0,3230,100,10,0,0,0,0,0",
        "#Y20496$3160,3140,[],0,0,0,4104,1,[20504],0,0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [9171, 20496],
        [],
        [],
        [],
        [],
    ]
    client.async_get_object_state.side_effect = [
        {
            "id": 9171,
            "type": 16,
            "stype": 3,
            "name": "ТП",
            "c": 36,
            "s": 25,
            "m": "heat",
        },
        {
            "id": 20496,
            "type": 16,
            "stype": 3,
            "name": "Радиаторы",
            "c": 42.5,
            "s": 41,
            "m": "heat",
        },
    ]

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert 9171 not in coordinator.data.heating_controls
    assert coordinator.data.heating_controls[20496].can_set_temperature
    assert set(coordinator.data.heating_states) == {9171, 20496}
    assert coordinator._updater.heating_metadata.refresh_needed


async def test_reconnect_requests_debounced_refresh(hass: HomeAssistant) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator._updater.mark_connection_stale = MagicMock()
    coordinator.async_request_refresh = AsyncMock()

    coordinator._async_connection_changed(True)
    await hass.async_block_till_done()

    coordinator._updater.mark_connection_stale.assert_called_once_with()
    coordinator.async_request_refresh.assert_awaited_once_with()


async def test_push_merges_partial_heating_circuit_state(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                8362: ZontHeatingCircuitData(
                    object_id=8362,
                    object_type=16,
                    name="ГВС",
                    subtype=1,
                    current_temperature=29,
                    target_temperature=60,
                    mode=ZontHeatingCircuitMode.HEAT,
                    mode_id=20501,
                    fault=False,
                )
            }
        ),
    )

    coordinator._async_message_received({"id": 8362, "c": 30.5})

    circuit = coordinator.data.objects[8362]
    assert isinstance(circuit, ZontHeatingCircuitData)
    assert circuit.current_temperature == 30.5
    assert circuit.target_temperature == 60
    assert circuit.mode is ZontHeatingCircuitMode.HEAT


async def test_push_preserves_internal_heating_state(hass: HomeAssistant) -> None:
    coordinator, _ = _coordinator(hass)
    internal_state = ZontHeatingCircuitInternalState(
        object_id=20496,
        target_sensor_id=4104,
        status_register=138,
    )
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                20496: ZontHeatingCircuitData(
                    object_id=20496,
                    object_type=16,
                    name="Радиаторы",
                    subtype=3,
                    current_temperature=42.5,
                    fault=False,
                )
            }
        ),
        heating_states=immutable_heating_states({20496: internal_state}),
    )

    coordinator._async_message_received({"id": 20496, "c": 43})

    assert coordinator.data.heating_states[20496] == internal_state


async def test_targeted_object_refresh_merges_controller_state(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                8362: ZontHeatingCircuitData(
                    object_id=8362,
                    object_type=16,
                    name="ГВС",
                    subtype=1,
                    current_temperature=29,
                    target_temperature=60,
                    mode=ZontHeatingCircuitMode.HEAT,
                )
            }
        ),
    )
    client.async_get_object_state.return_value = {
        "id": 8362,
        "type": 16,
        "stype": 1,
        "name": "ГВС",
        "c": 29.5,
        "s": 60.5,
        "m": "heat",
        "f": 0,
    }

    assert await coordinator.async_refresh_object(8362)

    circuit = coordinator.data.objects[8362]
    assert circuit.current_temperature == 29.5
    assert circuit.target_temperature == 60.5
    assert circuit.mode_id is None
    client.async_get_object_state.assert_awaited_once_with(8362)


async def test_targeted_failed_state_marks_object_unavailable(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({8362: ZontHeatingCircuitData(8362, 16, "ГВС")}),
    )
    client.async_get_object_state.return_value = {"id": 8362, "failed": 1}

    assert await coordinator.async_refresh_object(8362)
    assert not coordinator.data.objects[8362].available


async def test_push_merges_partial_radio_sensor_state(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                12001: ZontRadioSensorData(
                    object_id=12001,
                    object_type=8,
                    name="Гостиная",
                    subtype=18,
                    temperature=23.4,
                    humidity=48,
                    battery_voltage=2.91,
                    signal_strength_raw=86,
                )
            }
        ),
    )

    coordinator._async_message_received({"id": 12001, "h": 49})

    sensor = coordinator.data.objects[12001]
    assert isinstance(sensor, ZontRadioSensorData)
    assert sensor.temperature == 23.4
    assert sensor.humidity == 49
    assert sensor.battery_voltage == 2.91

    coordinator._async_message_received({"id": 12001, "a": 0})

    sensor = coordinator.data.objects[12001]
    assert not sensor.available
    assert sensor.humidity == 49


async def test_refresh_discovers_pump(hass: HomeAssistant) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [[], [], [], [], [], [9044], [], []]
    client.async_get_object_state.return_value = {
        "id": 9044,
        "type": 17,
        "name": "Насос Радиаторы",
        "s": 1,
    }

    await coordinator.async_refresh()

    pump = coordinator.data.objects[9044]
    assert isinstance(pump, ZontPumpData)
    assert pump.available
    assert pump.running
    client.async_get_object_state.assert_awaited_once_with(9044)
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=5.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=5.0),
        *_OPTIONAL_STATUS_CALLS,
    ]


async def test_push_updates_pump_running_state(hass: HomeAssistant) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {9044: ZontPumpData(9044, 17, "Насос Радиаторы", running=True)}
        ),
    )

    coordinator._async_message_received({"id": 9044, "s": 0})

    pump = coordinator.data.objects[9044]
    assert isinstance(pump, ZontPumpData)
    assert pump.available
    assert pump.running is False


async def test_refresh_discovers_mixer_and_internal_state(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Y9078$0,18",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [9078],
    ]
    client.async_get_object_state.return_value = {
        "id": 9078,
        "type": 15,
        "name": "Трехходовой ТП",
        "s": 0,
    }

    await coordinator.async_refresh()

    mixer = coordinator.data.objects[9078]
    assert isinstance(mixer, ZontMixerData)
    assert mixer.available
    assert mixer.direction is ZontMixerDirection.IDLE
    state = coordinator.data.mixer_states[9078]
    assert state.is_fully_closed
    assert not state.is_fully_open
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=5.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=5.0),
        *_OPTIONAL_STATUS_CALLS,
        call("#Y9078?"),
    ]


async def test_refresh_discovers_relay_and_caches_configuration(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_get_object_ids.relay_ids = [20488]
    client.async_get_object_state.return_value = {
        "id": 20488,
        "type": 14,
        "name": "Реле",
        "s": 1,
    }
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z20488:14,'Реле',255,9",
        "#Y20488$0",
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Y20488$2",
    ]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    relay = coordinator.data.objects[20488]
    assert isinstance(relay, ZontRelayData)
    assert relay.output_active
    assert coordinator.data.relay_configurations[20488] == (
        ZontRelayConfiguration(20488, 9)
    )
    assert coordinator.data.relay_states[20488] == ZontRelayInternalState(20488, 2)
    commands = [
        call.args[0] for call in client.async_send_system_command.await_args_list
    ]
    assert commands.count("#Z20488?") == 1
    assert commands.count("#Y20488?") == 2


async def test_invalid_relay_configuration_does_not_hide_relay_state(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_get_object_ids.relay_ids = [20488]
    client.async_get_object_state.return_value = {
        "id": 20488,
        "type": 14,
        "name": "Реле",
        "s": 1,
    }
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z20488:!",
        "#Y20488$2",
    ]

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert isinstance(coordinator.data.objects[20488], ZontRelayData)
    assert 20488 not in coordinator.data.relay_configurations
    assert coordinator.data.relay_states[20488].has_failed
    assert coordinator._updater.relay_metadata.refresh_needed


async def test_invalid_relay_state_does_not_hide_configuration(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_get_object_ids.relay_ids = [20488]
    client.async_get_object_state.return_value = {
        "id": 20488,
        "type": 14,
        "name": "Реле",
        "s": 1,
    }
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Z20488:14,'Реле',255,0",
        "#Y20488:!",
    ]

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.relay_configurations[20488] == (
        ZontRelayConfiguration(20488, 0)
    )
    assert 20488 not in coordinator.data.relay_states
    assert not coordinator._updater.relay_metadata.refresh_needed


async def test_relay_push_preserves_configuration_and_diagnostics(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    configuration = ZontRelayConfiguration(20488, 9)
    internal_state = ZontRelayInternalState(20488, 2)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {20488: ZontRelayData(20488, 14, "Реле", output_active=True)}
        ),
        relay_configurations=immutable_relay_configurations({20488: configuration}),
        relay_states=immutable_relay_states({20488: internal_state}),
    )

    coordinator._async_message_received({"id": 20488, "s": 0})

    relay = coordinator.data.objects[20488]
    assert isinstance(relay, ZontRelayData)
    assert relay.output_active is False
    assert coordinator.data.relay_configurations[20488] is configuration
    assert coordinator.data.relay_states[20488] is internal_state


async def test_invalid_mixer_state_is_isolated_from_other_mixers(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Y9078:!",
        "#Y9079$0,1",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [9078, 9079],
    ]
    client.async_get_object_state.side_effect = [
        {"id": 9078, "type": 15, "name": "Смеситель 1", "s": 0},
        {"id": 9079, "type": 15, "name": "Смеситель 2", "s": 0},
    ]

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert 9078 not in coordinator.data.mixer_states
    assert coordinator.data.mixer_states[9079].is_fully_open


async def test_refresh_clears_endpoint_flags_for_moving_mixer(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
        "#Y9078$1,19",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [9078],
    ]
    client.async_get_object_state.return_value = {
        "id": 9078,
        "type": 15,
        "name": "Трехходовой ТП",
        "s": 1,
    }

    await coordinator.async_refresh()

    state = coordinator.data.mixer_states[9078]
    assert not state.is_fully_open
    assert not state.is_fully_closed
    assert state.state_flags == 16


async def test_mixer_push_clears_stale_endpoint_but_preserves_faults(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                9078: ZontMixerData(
                    9078,
                    15,
                    "Трехходовой ТП",
                    direction=ZontMixerDirection.IDLE,
                )
            }
        ),
        mixer_states=immutable_mixer_states(
            {
                9078: ZontMixerInternalState(
                    object_id=9078,
                    direction=ZontMixerDirection.IDLE,
                    state_flags=97,
                )
            }
        ),
    )

    coordinator._async_message_received({"id": 9078, "s": 1})

    mixer = coordinator.data.objects[9078]
    assert isinstance(mixer, ZontMixerData)
    assert mixer.direction is ZontMixerDirection.OPENING
    state = coordinator.data.mixer_states[9078]
    assert not state.is_fully_open
    assert not state.is_fully_closed
    assert state.has_sensor_fault
    assert state.has_output_fault

    coordinator._async_message_received({"id": 9078, "s": 0})

    mixer = coordinator.data.objects[9078]
    assert isinstance(mixer, ZontMixerData)
    assert mixer.direction is ZontMixerDirection.IDLE
    assert coordinator.data.mixer_states[9078].state_flags == 96


async def test_refresh_discovers_ntc_temperature_sensor(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [[], [], [], [], [], [], [20487], []]
    client.async_get_object_state.return_value = {
        "id": 20487,
        "type": 27,
        "name": "Температура котла",
        "t": 45.6,
        "a": 1,
    }

    await coordinator.async_refresh()

    sensor = coordinator.data.objects[20487]
    assert isinstance(sensor, ZontNtcTemperatureSensorData)
    assert sensor.temperature == 45.6
    assert sensor.available
    assert client.async_get_object_ids.await_args_list == [
        call(0),
        call(1),
        call(2),
        call(6),
        call(8),
        call(10),
        call(16),
        call(17),
        call(27),
        call(15),
        call(14),
    ]
    client.async_get_object_state.assert_awaited_once_with(20487)


async def test_ntc_type_error_does_not_block_other_object_data(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                20487: ZontNtcTemperatureSensorData(
                    object_id=20487,
                    object_type=27,
                    name="Температура котла",
                    temperature=45.6,
                )
            }
        ),
    )
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [
        [],
        [],
        [4097],
        [],
        [],
        [],
        ZontProtocolError,
        [],
    ]
    client.async_get_object_state.return_value = {
        "id": 4097,
        "type": 6,
        "name": "Navien",
        "water": 35,
    }

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.objects[4097].available
    assert not coordinator.data.objects[20487].available


async def test_failed_ntc_sensor_preserves_last_temperature(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                20487: ZontNtcTemperatureSensorData(
                    object_id=20487,
                    object_type=27,
                    name="Температура котла",
                    temperature=45.6,
                )
            }
        ),
    )
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [[], [], [], [], [], [], [20487], []]
    client.async_get_object_state.return_value = {
        "id": 20487,
        "req_state": 0,
        "failed": 1,
    }

    await coordinator.async_refresh()

    sensor = coordinator.data.objects[20487]
    assert not sensor.available
    assert sensor.temperature == 45.6
    assert coordinator.last_update_success


async def test_push_updates_ntc_temperature_and_availability(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                20487: ZontNtcTemperatureSensorData(
                    object_id=20487,
                    object_type=27,
                    name="Температура котла",
                    temperature=45.6,
                )
            }
        ),
    )

    coordinator._async_message_received({"id": 20487, "t": 46.1})

    sensor = coordinator.data.objects[20487]
    assert isinstance(sensor, ZontNtcTemperatureSensorData)
    assert sensor.temperature == 46.1
    assert sensor.available

    coordinator._async_message_received({"id": 20487, "a": 0})

    sensor = coordinator.data.objects[20487]
    assert not sensor.available
    assert sensor.temperature == 46.1


async def test_push_can_discover_complete_ntc_temperature_sensor(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)

    coordinator._async_message_received(
        {
            "id": 20487,
            "type": 27,
            "name": "Температура котла",
            "t": 45.6,
            "a": 1,
        }
    )

    sensor = coordinator.data.objects[20487]
    assert isinstance(sensor, ZontNtcTemperatureSensorData)
    assert sensor.temperature == 45.6


async def test_push_merges_partial_adapter_state_without_resetting_schedule(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                4097: ZontDigitalBusAdapterData(
                    object_id=4097,
                    object_type=6,
                    name="Navien",
                    flow_temperature=35,
                    dhw_temperature=29,
                    state=ZontDigitalBusState.OFF,
                    error_code=0,
                )
            }
        ),
    )
    listener = MagicMock()
    unsubscribe = coordinator.async_add_listener(listener)
    scheduled = coordinator._unsub_refresh

    coordinator._async_message_received({"id": 4097, "water": 36.5, "state": 1})

    adapter = coordinator.data.objects[4097]
    assert adapter.flow_temperature == 36.5
    assert adapter.dhw_temperature == 29
    assert adapter.state is ZontDigitalBusState.RUNNING
    assert coordinator._unsub_refresh is scheduled
    listener.assert_called_once()

    unsubscribe()
    await coordinator.async_shutdown()


async def test_push_can_discover_complete_adapter(hass: HomeAssistant) -> None:
    coordinator, _ = _coordinator(hass)

    coordinator._async_message_received(
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": 35,
            "state": 0,
            "err": 0,
        }
    )

    assert coordinator.data.objects[4097].name == "Navien"
    assert coordinator.data.objects[4097].available


async def test_push_updates_temperature_and_availability(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                8196: ZontDigitalTemperatureSensorData(
                    object_id=8196,
                    object_type=1,
                    name="Погода из интернета",
                    temperature=19.7,
                )
            }
        ),
    )

    coordinator._async_message_received({"id": 8196, "t": 20.1})

    sensor = coordinator.data.objects[8196]
    assert isinstance(sensor, ZontDigitalTemperatureSensorData)
    assert sensor.temperature == 20.1
    assert sensor.available

    coordinator._async_message_received({"id": 8196, "a": 0})

    sensor = coordinator.data.objects[8196]
    assert not sensor.available
    assert sensor.temperature == 20.1


async def test_push_can_discover_complete_temperature_sensor(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)

    coordinator._async_message_received(
        {
            "id": 8196,
            "type": 1,
            "name": "Погода из интернета",
            "t": 19.7,
            "a": 1,
        }
    )

    sensor = coordinator.data.objects[8196]
    assert isinstance(sensor, ZontDigitalTemperatureSensorData)
    assert sensor.temperature == 19.7


async def test_start_and_shutdown_manage_message_listener(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    unsubscribe = MagicMock()
    client.async_add_message_listener.return_value = unsubscribe
    coordinator.async_request_refresh = AsyncMock()

    coordinator.async_start()
    await hass.async_block_till_done()

    client.async_add_message_listener.assert_called_once_with(
        coordinator._async_message_received
    )

    await coordinator.async_shutdown()
    unsubscribe.assert_called_once_with()


async def test_trigger_push_coalesces_addressed_refreshes_for_all_security_zones(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                4118: ZontAnalogInputData(
                    4118,
                    0,
                    "HA - Тестовая дверь",
                    subtype=20,
                    value=0,
                    unit_code=0,
                    triggered=False,
                ),
                10657: ZontSecurityZoneData(
                    10657,
                    2,
                    "Тестовая зона",
                    armed=True,
                    triggered=False,
                ),
                10658: ZontSecurityZoneData(
                    10658,
                    2,
                    "Вторая зона",
                    armed=False,
                    triggered=False,
                ),
            }
        ),
    )
    client.async_get_object_state.side_effect = [
        {
            "id": 10657,
            "type": 2,
            "name": "Тестовая зона",
            "s": 1,
            "trig": 1,
        },
        {
            "id": 10658,
            "type": 2,
            "name": "Вторая зона",
            "s": 0,
            "trig": 0,
        },
    ]
    coordinator._security_zone_push_debouncer.async_schedule_call = MagicMock()

    for triggered in (0, 1, 1):
        coordinator._async_message_received(
            {
                "id": 4118,
                "type": 0,
                "stype": 20,
                "name": "HA - Тестовая дверь",
                "v": 2,
                "u": 0,
                "a": 1,
                "trig": triggered,
            }
        )

    assert coordinator._pending_security_zone_ids == {10657, 10658}
    await coordinator._async_refresh_pending_security_zones()

    assert client.async_get_object_state.await_args_list == [call(10657), call(10658)]
    assert coordinator.data.objects[10657].triggered is True
    assert coordinator.data.objects[10658].armed is False
    assert not coordinator._pending_security_zone_ids


def test_direct_security_zone_push_does_not_schedule_redundant_refresh(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _coordinator(hass)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                10657: ZontSecurityZoneData(
                    10657,
                    2,
                    "Тестовая зона",
                    armed=False,
                    triggered=False,
                )
            }
        ),
    )
    coordinator._security_zone_push_debouncer.async_schedule_call = MagicMock()

    coordinator._async_message_received({"id": 10657, "s": 1, "trig": 1})

    zone = coordinator.data.objects[10657]
    assert isinstance(zone, ZontSecurityZoneData)
    assert zone.armed is True
    assert zone.triggered is True
    coordinator._security_zone_push_debouncer.async_schedule_call.assert_not_called()
