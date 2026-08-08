"""Tests for the shared ZONT data coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

from custom_components.zont_ws.client import ZontProtocolError, ZontWsClient
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.controller import (
    COMMAND_SERVER_INFO,
    COMMAND_SUPPLY_VOLTAGE,
    ZontCommunicationChannel,
)
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
    ZontDataUpdateCoordinator,
)
from custom_components.zont_ws.objects import (
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontNtcTemperatureSensorData,
    ZontRadioSensorData,
    immutable_objects,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _coordinator(
    hass: HomeAssistant,
) -> tuple[ZontDataUpdateCoordinator, MagicMock]:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="ABCDEF123456", data={})
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.async_send_system_command = AsyncMock()
    client.async_get_object_ids = AsyncMock(return_value=[])
    client.async_get_object_state = AsyncMock()
    coordinator = ZontDataUpdateCoordinator(
        hass,
        entry,
        client,
        initial_info=None,
        on_controller_info=MagicMock(),
    )
    return coordinator, client


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
        call(COMMAND_SERVER_INFO, response_timeout=3.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=3.0),
    ]
    assert client.async_get_object_ids.await_args_list == [
        call(0),
        call(1),
        call(6),
        call(8),
        call(27),
    ]


async def test_invalid_source_is_disabled_without_breaking_others(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:!",
        "#S6:123 0",
        "#S6:124 0",
    ]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.controller.server_status is None
    assert coordinator.data.controller.supply_voltage == 12.4
    assert coordinator.disabled_sources == ("server_status",)
    assert client.async_send_system_command.await_args_list == [
        call(COMMAND_SERVER_INFO, response_timeout=3.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=3.0),
        call(COMMAND_SUPPLY_VOLTAGE, response_timeout=3.0),
    ]


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
    client.async_get_object_ids.side_effect = [[], [], [4097], [], []]
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
        [4097],
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
        [4097],
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
    client.async_get_object_ids.side_effect = [[], [8196], [4097], [], []]
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
        call(6),
        call(8),
        call(27),
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
    client.async_get_object_ids.side_effect = [[20550], [], [], [], []]
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
        call(6),
        call(8),
        call(27),
    ]


async def test_invalid_analog_input_does_not_block_other_object_types(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [[20550], [], [4097], [], []]
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
    client.async_get_object_ids.side_effect = [[], [], [], [12001], []]
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
        call(6),
        call(8),
        call(27),
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
        [20487],
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


async def test_refresh_discovers_ntc_temperature_sensor(
    hass: HomeAssistant,
) -> None:
    coordinator, client = _coordinator(hass)
    client.async_send_system_command.side_effect = [
        "#S224:1 0 1 0",
        "#S6:123 0",
    ]
    client.async_get_object_ids.side_effect = [[], [], [], [], [20487]]
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
        call(6),
        call(8),
        call(27),
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
        ZontProtocolError,
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
    client.async_get_object_ids.side_effect = [[], [], [], [], [20487]]
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
    coordinator.async_refresh = AsyncMock()

    coordinator.async_start()
    await hass.async_block_till_done()

    client.async_add_message_listener.assert_called_once_with(
        coordinator._async_message_received
    )

    await coordinator.async_shutdown()
    unsubscribe.assert_called_once_with()
