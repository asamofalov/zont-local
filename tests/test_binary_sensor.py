"""Tests for the ZONT connectivity sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.zont_ws.binary_sensor import async_setup_entry
from custom_components.zont_ws.const import DOMAIN, connection_signal
from custom_components.zont_ws.coordinator import (
    ZontDataUpdateCoordinator,
)
from custom_components.zont_ws.data import ZontControllerData, ZontData
from custom_components.zont_ws.entities.analog_input import (
    ZontAnalogInputTriggeredBinarySensor,
)
from custom_components.zont_ws.entities.controller import (
    ZontCloudConnectedBinarySensor,
    ZontConnectedBinarySensor,
    ZontEthernetConnectedBinarySensor,
    ZontWifiConnectedBinarySensor,
)
from custom_components.zont_ws.entities.heating.states import (
    HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE,
    HEATING_CIRCUIT_FAULT_DESCRIPTION,
    HEATING_CIRCUIT_HEATING_DESCRIPTION,
    ZontHeatingCircuitBinarySensor,
)
from custom_components.zont_ws.entities.mixer import (
    MIXER_BINARY_SENSOR_DESCRIPTIONS,
    ZontMixerBinarySensor,
)
from custom_components.zont_ws.entities.pump import ZontPumpRunningBinarySensor
from custom_components.zont_ws.entities.radio import (
    ZontRadioTriggeredBinarySensor,
)
from custom_components.zont_ws.entities.relay import (
    ZontRelayFailedBinarySensor,
)
from custom_components.zont_ws.protocol import ZontClient
from custom_components.zont_ws.protocol.controller import (
    ZontCommunicationChannel,
    ZontEthernetStatus,
    ZontServerStatus,
    ZontWifiStatus,
)
from custom_components.zont_ws.protocol.heating_config import (
    ZontConsumerControlMode,
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
    immutable_heating_controls,
    immutable_heating_states,
)
from custom_components.zont_ws.protocol.mixer import (
    ZontMixerInternalState,
    immutable_mixer_states,
)
from custom_components.zont_ws.protocol.objects import (
    ZontAnalogInputData,
    ZontHeatingCircuitData,
    ZontHeatingCircuitMode,
    ZontMixerData,
    ZontMixerDirection,
    ZontObject,
    ZontPumpData,
    ZontRadioSensorData,
    ZontRelayData,
    immutable_objects,
)
from custom_components.zont_ws.protocol.relay import (
    ZontRelayInternalState,
    immutable_relay_states,
)
from custom_components.zont_ws.runtime import ZontRuntimeData
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
    client = MagicMock(spec=ZontClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({obj.object_id: obj}),
    )
    coordinator.async_add_listener.return_value = lambda: None
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


def _relay_entry(state: ZontRelayInternalState | None) -> MockConfigEntry:
    relay = ZontRelayData(20488, 14, "Реле", output_active=True)
    entry = _object_entry(relay)
    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({20488: relay}),
        relay_states=immutable_relay_states(
            {20488: state} if state is not None else None
        ),
    )
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
    client = MagicMock(spec=ZontClient)
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
    client = MagicMock(spec=ZontClient)
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


def test_extended_connection_sensors_use_link_status() -> None:
    """Expose access-point and Ethernet link state separately from cloud channels."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(
            info=None,
            wifi_status=ZontWifiStatus(connected=True, raw_signal=86),
            ethernet_status=ZontEthernetStatus(connected=False),
        )
    )
    entry.runtime_data = ZontRuntimeData(client, coordinator)

    wifi = ZontWifiConnectedBinarySensor(entry)
    ethernet = ZontEthernetConnectedBinarySensor(entry)

    assert wifi.available
    assert wifi.is_on
    assert ethernet.available
    assert not ethernet.is_on
    assert wifi.device_class is BinarySensorDeviceClass.CONNECTIVITY
    assert ethernet.device_class is BinarySensorDeviceClass.CONNECTIVITY
    assert wifi.entity_category is EntityCategory.DIAGNOSTIC
    assert wifi.unique_id == "ABCDEF123456_wifi_connected"
    assert ethernet.unique_id == "ABCDEF123456_ethernet_connected"


async def test_setup_adds_only_supported_controller_connections(
    hass: HomeAssistant,
) -> None:
    """Do not create Ethernet when its optional source is unsupported."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(
            info=None,
            wifi_status=ZontWifiStatus(connected=True, raw_signal=86),
        )
    )
    coordinator.async_add_listener.return_value = lambda: None
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    optional_entities = async_add_entities.call_args_list[1].args[0]
    assert len(optional_entities) == 1
    assert isinstance(optional_entities[0], ZontWifiConnectedBinarySensor)
    controller_listener = coordinator.async_add_listener.call_args_list[0].args[0]
    controller_listener()
    assert async_add_entities.call_count == 2


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


@pytest.mark.parametrize(("running", "is_on"), [(True, True), (False, False)])
def test_pump_binary_sensor_uses_observed_state(
    running: bool,
    is_on: bool,
) -> None:
    pump = ZontPumpData(9044, 17, "Насос Радиаторы", running=running)
    entry = _object_entry(pump)

    entity = ZontPumpRunningBinarySensor(entry, 9044)

    assert entity.available
    assert entity.is_on is is_on
    assert entity.name is None
    assert entity.device_class is BinarySensorDeviceClass.RUNNING
    assert entity.entity_category is None
    assert entity.unique_id == "ABCDEF123456_9044_running"
    assert entity.suggested_object_id is None
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:9044")}


def test_pump_binary_sensor_tracks_availability() -> None:
    pump = ZontPumpData(
        9044,
        17,
        "Насос Радиаторы",
        available=False,
        running=True,
    )
    entry = _object_entry(pump)

    entity = ZontPumpRunningBinarySensor(entry, 9044)

    assert not entity.available
    assert entity.is_on


async def test_setup_adds_pump_without_duplicates(hass: HomeAssistant) -> None:
    pump = ZontPumpData(9044, 17, "Насос Радиаторы", running=True)
    entry = _object_entry(pump)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontPumpRunningBinarySensor)
    assert entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2


@pytest.mark.parametrize(
    ("key", "flags", "expected"),
    [
        ("sensor_fault", 32, True),
        ("sensor_fault", 0, False),
        ("output_fault", 64, True),
        ("output_fault", 0, False),
        ("set_failed", 128, True),
        ("set_failed", 0, False),
    ],
)
def test_mixer_binary_sensor_uses_internal_flags(
    key: str,
    flags: int,
    expected: bool,
) -> None:
    mixer = ZontMixerData(
        9078,
        15,
        "Трехходовой ТП",
        direction=ZontMixerDirection.IDLE,
    )
    entry = _object_entry(mixer)
    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({9078: mixer}),
        mixer_states=immutable_mixer_states(
            {
                9078: ZontMixerInternalState(
                    object_id=9078,
                    direction=ZontMixerDirection.IDLE,
                    state_flags=flags,
                )
            }
        ),
    )
    description = next(
        item for item in MIXER_BINARY_SENSOR_DESCRIPTIONS if item.key == key
    )

    entity = ZontMixerBinarySensor(entry, 9078, description)

    assert entity.available
    assert entity.is_on is expected
    assert entity.device_class is BinarySensorDeviceClass.PROBLEM
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.unique_id == f"ABCDEF123456_9078_{key}"
    assert entity.suggested_object_id == key


def test_mixer_binary_sensor_is_unavailable_without_internal_state() -> None:
    mixer = ZontMixerData(
        9078,
        15,
        "Трехходовой ТП",
        direction=ZontMixerDirection.OPENING,
    )
    entry = _object_entry(mixer)
    entity = ZontMixerBinarySensor(entry, 9078, MIXER_BINARY_SENSOR_DESCRIPTIONS[0])

    assert not entity.available
    assert entity.is_on is None


async def test_setup_adds_mixer_diagnostics_without_duplicates(
    hass: HomeAssistant,
) -> None:
    mixer = ZontMixerData(
        9078,
        15,
        "Трехходовой ТП",
        direction=ZontMixerDirection.IDLE,
    )
    entry = _object_entry(mixer)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert {entity.entity_description.key for entity in entities} == {
        "sensor_fault",
        "output_fault",
        "set_failed",
    }
    assert all(isinstance(entity, ZontMixerBinarySensor) for entity in entities)
    assert all(not entity.available for entity in entities)

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
    ("key", "device_class", "entity_category"),
    [
        ("weather_compensation", None, EntityCategory.DIAGNOSTIC),
        ("blocked", BinarySensorDeviceClass.PROBLEM, EntityCategory.DIAGNOSTIC),
        (
            "sensor_fault",
            BinarySensorDeviceClass.PROBLEM,
            EntityCategory.DIAGNOSTIC,
        ),
        ("summer_mode", None, None),
        ("fault", BinarySensorDeviceClass.PROBLEM, EntityCategory.DIAGNOSTIC),
    ],
)
def test_heating_circuit_binary_sensors_use_independent_sources(
    key: str,
    device_class: BinarySensorDeviceClass | None,
    entity_category: EntityCategory | None,
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
    assert entity.entity_category is entity_category
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


@pytest.mark.parametrize(
    ("mode", "status_register", "available", "is_on"),
    [
        (ZontHeatingCircuitMode.HEAT, 1, True, True),
        (ZontHeatingCircuitMode.HEAT, 0, True, False),
        (ZontHeatingCircuitMode.HEAT, None, False, None),
        (ZontHeatingCircuitMode.OFF, None, True, False),
    ],
)
def test_dhw_heating_uses_internal_activity(
    mode: ZontHeatingCircuitMode,
    status_register: int | None,
    available: bool,
    is_on: bool | None,
) -> None:
    dhw = ZontHeatingCircuitData(
        8362,
        16,
        "ГВС",
        subtype=1,
        mode=mode,
        fault=False,
    )
    entry = _object_entry(dhw)
    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({8362: dhw}),
        heating_states=immutable_heating_states(
            {
                8362: ZontHeatingCircuitInternalState(
                    object_id=8362,
                    target_sensor_id=4097,
                    status_register=status_register,
                )
            }
            if status_register is not None
            else None
        ),
    )

    entity = ZontHeatingCircuitBinarySensor(
        entry,
        8362,
        HEATING_CIRCUIT_HEATING_DESCRIPTION,
    )

    assert entity.available is available
    assert entity.is_on is is_on
    assert entity.device_class is BinarySensorDeviceClass.RUNNING
    assert entity.entity_category is None
    assert entity.unique_id == "ABCDEF123456_8362_heating"
    assert entity.suggested_object_id == "heating"


async def test_setup_adds_heating_states_by_circuit_subtype(
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
    assert {entity.entity_description.key for entity in dhw_entities} == {
        "heating",
        "fault",
    }
    assert next(
        entity for entity in dhw_entities if entity.entity_description.key == "fault"
    ).available

    dhw_listener = dhw_entry.runtime_data.coordinator.async_add_listener.call_args.args[
        0
    ]
    dhw_listener()
    assert dhw_add_entities.call_count == 2


@pytest.mark.parametrize(("flags", "failed"), [(0, False), (2, True), (15, True)])
def test_relay_failure_sensor(flags: int, failed: bool) -> None:
    entry = _relay_entry(ZontRelayInternalState(20488, flags))

    entity = ZontRelayFailedBinarySensor(entry, 20488)

    assert entity.available
    assert entity.is_on is failed
    assert entity.device_class is BinarySensorDeviceClass.PROBLEM
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.unique_id == "ABCDEF123456_20488_failed"
    assert entity.suggested_object_id == "failed"


def test_relay_failure_sensor_requires_internal_state() -> None:
    entity = ZontRelayFailedBinarySensor(_relay_entry(None), 20488)

    assert not entity.available
    assert entity.is_on is None


async def test_setup_adds_relay_failure_sensor_once(hass: HomeAssistant) -> None:
    entry = _relay_entry(ZontRelayInternalState(20488, 0))
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontRelayFailedBinarySensor)

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2
