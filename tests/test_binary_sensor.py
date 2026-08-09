"""Tests for the ZONT connectivity sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.zont_ws.binary_sensor import (
    HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE,
    HEATING_CIRCUIT_FAULT_DESCRIPTION,
    ZontAnalogInputTriggeredBinarySensor,
    ZontCloudConnectedBinarySensor,
    ZontConnectedBinarySensor,
    ZontHeatingCircuitBinarySensor,
    ZontRadioTriggeredBinarySensor,
    async_setup_entry,
)
from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import DOMAIN, connection_signal
from custom_components.zont_ws.controller import (
    ZontCommunicationChannel,
    ZontServerStatus,
)
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
    ZontDataUpdateCoordinator,
    ZontRuntimeData,
)
from custom_components.zont_ws.heating_config import (
    ZontConsumerControlMode,
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
    immutable_heating_controls,
    immutable_heating_states,
)
from custom_components.zont_ws.objects import (
    ZontAnalogInputData,
    ZontHeatingCircuitData,
    ZontObject,
    ZontRadioSensorData,
    immutable_objects,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _object_entry(obj: ZontObject) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({obj.object_id: obj}),
    )
    coordinator.async_add_listener.return_value = lambda: None
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


def _consumer_entry(
    *,
    fault: bool | None = True,
    has_weather_compensation: bool = True,
    status_register: int | None = 138,
) -> MockConfigEntry:
    circuit = ZontHeatingCircuitData(
        object_id=9825,
        object_type=16,
        name="Кабинет",
        subtype=3,
        fault=fault,
    )
    entry = _object_entry(circuit)
    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({9825: circuit}),
        heating_controls=immutable_heating_controls(
            {
                9825: ZontHeatingCircuitControlData(
                    control_mode=ZontConsumerControlMode.AIR_PID,
                    has_weather_compensation=has_weather_compensation,
                    target_sensor_id=4110,
                    min_temperature=10,
                    max_temperature=40,
                )
            }
        ),
        heating_states=immutable_heating_states(
            {
                9825: ZontHeatingCircuitInternalState(
                    object_id=9825,
                    target_sensor_id=4110,
                    status_register=status_register,
                )
            }
        ),
    )
    return entry


async def test_connection_state_updates(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    entity = ZontConnectedBinarySensor(entry)
    entity.hass = hass
    entity.entity_id = "binary_sensor.zont_connected"
    entity.async_write_ha_state = MagicMock()

    assert entity.is_on
    assert entity.unique_id == "ABCDEF123456_connected"
    assert entity.suggested_object_id == "connected"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456")}
    assert "name" not in entity.device_info
    assert "model" not in entity.device_info
    await entity.async_added_to_hass()
    async_dispatcher_send(hass, connection_signal(entry.entry_id), False)

    assert not entity.is_on
    assert entity.available
    entity.async_write_ha_state.assert_called_once()
    await entity.async_remove()


async def test_cloud_connection_uses_shared_snapshot(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(
            info=None,
            server_status=ZontServerStatus(
                cloud_connected=True,
                channels=frozenset({ZontCommunicationChannel.WIFI}),
            ),
        )
    )
    entry.runtime_data = ZontRuntimeData(client, coordinator)

    entity = ZontCloudConnectedBinarySensor(entry)

    assert entity.available
    assert entity.is_on
    assert entity.unique_id == "ABCDEF123456_cloud_connected"
    assert entity.suggested_object_id == "cloud_connected"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456")}


@pytest.mark.parametrize(
    ("subtype", "device_class"),
    [
        (0, BinarySensorDeviceClass.PROBLEM),
        (1, BinarySensorDeviceClass.PROBLEM),
        (2, BinarySensorDeviceClass.PROBLEM),
        (3, BinarySensorDeviceClass.DOOR),
        (4, BinarySensorDeviceClass.MOTION),
        (5, BinarySensorDeviceClass.SMOKE),
        (6, BinarySensorDeviceClass.MOISTURE),
        (7, BinarySensorDeviceClass.MOTION),
        (8, BinarySensorDeviceClass.PROBLEM),
        (9, BinarySensorDeviceClass.PROBLEM),
        (10, BinarySensorDeviceClass.PROBLEM),
        (11, BinarySensorDeviceClass.POWER),
        (12, BinarySensorDeviceClass.PROBLEM),
        (13, BinarySensorDeviceClass.PROBLEM),
        (14, None),
        (15, BinarySensorDeviceClass.SAFETY),
        (16, BinarySensorDeviceClass.PROBLEM),
        (17, BinarySensorDeviceClass.PROBLEM),
        (18, BinarySensorDeviceClass.PROBLEM),
        (19, None),
        (20, None),
        (21, BinarySensorDeviceClass.PROBLEM),
        (22, BinarySensorDeviceClass.PROBLEM),
    ],
)
def test_analog_trigger_maps_subtype_device_class(
    subtype: int,
    device_class: BinarySensorDeviceClass | None,
) -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход",
        subtype=subtype,
        value=1,
        unit_code=8,
        triggered=True,
    )
    entry = _object_entry(analog_input)

    entity = ZontAnalogInputTriggeredBinarySensor(entry, 20550, subtype)

    assert entity.available
    assert entity.is_on
    assert entity.device_class is device_class
    assert entity.unique_id == "ABCDEF123456_20550_triggered"
    assert entity.suggested_object_id == "triggered"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:20550")}


def test_analog_trigger_tracks_field_and_object_availability() -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход",
        subtype=3,
        value=0,
        triggered=None,
    )
    entry = _object_entry(analog_input)
    entity = ZontAnalogInputTriggeredBinarySensor(entry, 20550, 3)

    assert not entity.available

    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                20550: ZontAnalogInputData(
                    object_id=20550,
                    object_type=0,
                    name="Вход",
                    available=False,
                    subtype=3,
                    value=0,
                    triggered=True,
                )
            }
        ),
    )

    assert not entity.available
    assert entity.is_on


async def test_setup_adds_analog_trigger_without_duplicates(
    hass: HomeAssistant,
) -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход",
        available=False,
        subtype=3,
    )
    entry = _object_entry(analog_input)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert len(async_add_entities.call_args_list[0].args[0]) == 2
    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontAnalogInputTriggeredBinarySensor)
    assert not entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2


@pytest.mark.parametrize(
    ("subtype", "device_class"),
    [
        (10, BinarySensorDeviceClass.MOISTURE),
        (11, BinarySensorDeviceClass.MOTION),
    ],
)
def test_radio_trigger_maps_supported_subtype_device_class(
    subtype: int,
    device_class: BinarySensorDeviceClass,
) -> None:
    radio_sensor = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Радиодатчик",
        subtype=subtype,
        triggered=True,
    )
    entry = _object_entry(radio_sensor)

    entity = ZontRadioTriggeredBinarySensor(entry, 12001, subtype)

    assert entity.available
    assert entity.is_on
    assert entity.device_class is device_class
    assert entity.unique_id == "ABCDEF123456_12001_triggered"
    assert entity.suggested_object_id == "triggered"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:12001")}


async def test_setup_adds_only_supported_radio_triggers(hass: HomeAssistant) -> None:
    radio_sensor = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Протечка",
        available=False,
        subtype=10,
    )
    entry = _object_entry(radio_sensor)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontRadioTriggeredBinarySensor)
    assert not entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2


async def test_setup_skips_radio_trigger_for_other_subtypes(
    hass: HomeAssistant,
) -> None:
    radio_sensor = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Радиотермометр",
        subtype=5,
        triggered=True,
    )
    entry = _object_entry(radio_sensor)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert async_add_entities.call_count == 1


@pytest.mark.parametrize(
    ("key", "device_class"),
    [
        ("weather_compensation", None),
        ("blocked", BinarySensorDeviceClass.PROBLEM),
        ("sensor_fault", BinarySensorDeviceClass.PROBLEM),
        ("summer_mode", None),
        ("fault", BinarySensorDeviceClass.PROBLEM),
    ],
)
def test_heating_circuit_binary_sensors_use_independent_sources(
    key: str,
    device_class: BinarySensorDeviceClass | None,
) -> None:
    entry = _consumer_entry()
    description = next(
        item
        for item in HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE[3]
        if item.key == key
    )

    entity = ZontHeatingCircuitBinarySensor(entry, 9825, description)

    assert entity.available
    assert entity.is_on
    assert entity.device_class is device_class
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.entity_registry_enabled_default
    assert entity.unique_id == f"ABCDEF123456_9825_{key}"
    assert entity.suggested_object_id == key
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:9825")}


def test_heating_circuit_binary_sensors_are_unavailable_without_sources() -> None:
    circuit = ZontHeatingCircuitData(
        object_id=9825,
        object_type=16,
        name="Кабинет",
        subtype=3,
        fault=None,
    )
    entry = _object_entry(circuit)

    entities = [
        ZontHeatingCircuitBinarySensor(entry, 9825, description)
        for description in HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE[3]
    ]

    assert all(not entity.available for entity in entities)
    assert all(entity.is_on is None for entity in entities)


def test_heating_circuit_false_states_remain_available() -> None:
    entry = _consumer_entry(
        fault=False,
        has_weather_compensation=False,
        status_register=0,
    )

    entities = [
        ZontHeatingCircuitBinarySensor(entry, 9825, description)
        for description in HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE[3]
    ]

    assert all(entity.available for entity in entities)
    assert all(entity.is_on is False for entity in entities)


@pytest.mark.parametrize(
    ("fault", "available", "is_on"),
    [
        (True, True, True),
        (False, True, False),
        (None, False, None),
    ],
)
def test_dhw_fault_uses_websocket_state(
    fault: bool | None,
    available: bool,
    is_on: bool | None,
) -> None:
    dhw = ZontHeatingCircuitData(8362, 16, "ГВС", subtype=1, fault=fault)
    entry = _object_entry(dhw)

    entity = ZontHeatingCircuitBinarySensor(
        entry,
        8362,
        HEATING_CIRCUIT_FAULT_DESCRIPTION,
    )

    assert entity.available is available
    assert entity.is_on is is_on
    assert entity.device_class is BinarySensorDeviceClass.PROBLEM
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.entity_registry_enabled_default
    assert entity.unique_id == "ABCDEF123456_8362_fault"
    assert entity.suggested_object_id == "fault"


def test_dhw_fault_tracks_object_availability() -> None:
    dhw = ZontHeatingCircuitData(
        8362,
        16,
        "ГВС",
        available=False,
        subtype=1,
        fault=True,
    )
    entry = _object_entry(dhw)

    entity = ZontHeatingCircuitBinarySensor(
        entry,
        8362,
        HEATING_CIRCUIT_FAULT_DESCRIPTION,
    )

    assert not entity.available
    assert entity.is_on


async def test_setup_adds_heating_diagnostics_by_circuit_subtype(
    hass: HomeAssistant,
) -> None:
    entry = _consumer_entry()
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert {entity.entity_description.key for entity in entities} == {
        "weather_compensation",
        "blocked",
        "sensor_fault",
        "summer_mode",
        "fault",
    }
    assert all(entity.available for entity in entities)

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2

    dhw = ZontHeatingCircuitData(8362, 16, "ГВС", subtype=1, fault=True)
    dhw_entry = _object_entry(dhw)
    dhw_entry.add_to_hass(hass)
    dhw_add_entities = MagicMock()

    await async_setup_entry(hass, dhw_entry, dhw_add_entities)

    dhw_entities = dhw_add_entities.call_args_list[1].args[0]
    assert len(dhw_entities) == 1
    assert dhw_entities[0].entity_description.key == "fault"
    assert dhw_entities[0].available

    dhw_listener = dhw_entry.runtime_data.coordinator.async_add_listener.call_args.args[
        0
    ]
    dhw_listener()
    assert dhw_add_entities.call_count == 2
