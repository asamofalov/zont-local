"""Tests for ZONT controller sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import DOMAIN
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
from custom_components.zont_ws.objects import (
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontObject,
    immutable_objects,
)
from custom_components.zont_ws.sensor import (
    ANALOG_INPUT_UNITS,
    CONNECTION_CHANNEL_STATES,
    DIGITAL_BUS_SENSOR_DESCRIPTIONS,
    ZontAnalogInputValueSensor,
    ZontConnectionChannelSensor,
    ZontDigitalBusSensor,
    ZontDigitalTemperatureSensor,
    ZontSupplyVoltageSensor,
    async_setup_entry,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(
    controller: ZontControllerData,
    objects: dict[int, ZontObject] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=controller,
        objects=immutable_objects(objects),
    )
    coordinator.async_add_listener.return_value = lambda: None
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


def test_connection_channel_sensor_uses_enum_state() -> None:
    entry = _entry(
        ZontControllerData(
            info=None,
            server_status=ZontServerStatus(
                cloud_connected=True,
                channels=frozenset(
                    {
                        ZontCommunicationChannel.GSM,
                        ZontCommunicationChannel.WIFI,
                    }
                ),
            ),
        )
    )

    entity = ZontConnectionChannelSensor(entry)

    assert entity.available
    assert entity.native_value == "gsm_wifi"
    assert entity.options == list(CONNECTION_CHANNEL_STATES)
    assert entity.device_class is SensorDeviceClass.ENUM
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.unique_id == "ABCDEF123456_connection_channel"
    assert entity.suggested_object_id == "connection_channel"


def test_supply_voltage_sensor_uses_native_measurement() -> None:
    entry = _entry(ZontControllerData(info=None, supply_voltage=12.3))

    entity = ZontSupplyVoltageSensor(entry)

    assert entity.available
    assert entity.native_value == 12.3
    assert entity.device_class is SensorDeviceClass.VOLTAGE
    assert entity.native_unit_of_measurement is UnitOfElectricPotential.VOLT
    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.suggested_display_precision == 1
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.unique_id == "ABCDEF123456_supply_voltage"
    assert entity.suggested_object_id == "supply_voltage"


def test_controller_sensors_are_unavailable_without_source_data() -> None:
    entry = _entry(ZontControllerData(info=None))

    assert not ZontConnectionChannelSensor(entry).available
    assert not ZontSupplyVoltageSensor(entry).available


@pytest.mark.parametrize(
    ("unit_code", "subtype", "unit", "device_class"),
    [
        (0, 0, UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
        (1, 0, "kΩ", None),
        (2, 1, UnitOfPressure.BAR, SensorDeviceClass.PRESSURE),
        (3, 12, UnitOfSpeed.KILOMETERS_PER_HOUR, SensorDeviceClass.SPEED),
        (4, 13, "rpm", None),
        (5, 16, UnitOfVolume.LITERS, SensorDeviceClass.VOLUME),
        (
            6,
            16,
            UnitOfVolumeFlowRate.LITERS_PER_HOUR,
            SensorDeviceClass.VOLUME_FLOW_RATE,
        ),
        (7, 17, PERCENTAGE, SensorDeviceClass.HUMIDITY),
        (7, 0, PERCENTAGE, None),
        (8, 0, None, None),
        (99, 22, None, None),
    ],
)
def test_analog_input_sensor_maps_documented_units(
    unit_code: int,
    subtype: int,
    unit: str | None,
    device_class: SensorDeviceClass | None,
) -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Аналоговый вход",
        subtype=subtype,
        value=12.2,
        unit_code=unit_code,
        triggered=False,
    )
    entry = _entry(ZontControllerData(info=None), {20550: analog_input})

    entity = ZontAnalogInputValueSensor(entry, 20550, subtype)

    assert entity.available
    assert entity.native_value == 12.2
    assert entity.native_unit_of_measurement == unit
    assert entity.device_class is device_class
    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.unique_id == "ABCDEF123456_20550_value"
    assert entity.suggested_object_id == "value"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:20550")}


def test_analog_binary_first_raw_value_is_disabled_diagnostic() -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход двери",
        subtype=3,
        value=0,
        unit_code=8,
        triggered=False,
    )
    entry = _entry(ZontControllerData(info=None), {20550: analog_input})

    entity = ZontAnalogInputValueSensor(entry, 20550, 3)

    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert not entity.entity_registry_enabled_default


def test_analog_input_sensor_tracks_value_and_object_availability() -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход",
        subtype=0,
        value=None,
        unit_code=0,
        triggered=False,
    )
    entry = _entry(ZontControllerData(info=None), {20550: analog_input})
    entity = ZontAnalogInputValueSensor(entry, 20550, 0)

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
                    subtype=0,
                    value=12.2,
                    unit_code=0,
                    triggered=False,
                )
            }
        ),
    )

    assert not entity.available
    assert entity.native_value == 12.2


def test_all_documented_analog_input_units_are_mapped() -> None:
    assert set(ANALOG_INPUT_UNITS) == set(range(9))


def test_digital_bus_sensors_expose_all_documented_fields() -> None:
    adapter = ZontDigitalBusAdapterData(
        object_id=4097,
        object_type=6,
        name="Navien",
        flow_temperature=45.6,
        dhw_temperature=34.5,
        return_temperature=30.4,
        modulation=99,
        pressure=2.4,
        state=ZontDigitalBusState.RUNNING,
        error_code=0,
    )
    entry = _entry(ZontControllerData(info=None), {4097: adapter})

    entities = {
        description.key: ZontDigitalBusSensor(entry, 4097, description)
        for description in DIGITAL_BUS_SENSOR_DESCRIPTIONS
    }

    assert entities["flow_temperature"].native_value == 45.6
    assert entities["dhw_temperature"].native_value == 34.5
    assert entities["return_temperature"].native_value == 30.4
    assert entities["modulation"].native_value == 99
    assert entities["pressure"].native_value == 2.4
    assert entities["state"].native_value is ZontDigitalBusState.RUNNING
    assert entities["state"].options == ["off", "running", "error"]
    assert entities["error_code"].native_value == 0
    assert entities["error_code"].entity_category is EntityCategory.DIAGNOSTIC
    assert entities["flow_temperature"].unique_id == (
        "ABCDEF123456_4097_flow_temperature"
    )
    assert entities["flow_temperature"].suggested_object_id == "flow_temperature"
    assert entities["flow_temperature"].device_info["identifiers"] == {
        (DOMAIN, "ABCDEF123456:object:4097")
    }


def test_digital_bus_sensor_tracks_object_and_field_availability() -> None:
    adapter = ZontDigitalBusAdapterData(4097, 6, "Navien")
    entry = _entry(ZontControllerData(info=None), {4097: adapter})
    entity = ZontDigitalBusSensor(entry, 4097, DIGITAL_BUS_SENSOR_DESCRIPTIONS[0])

    assert not entity.available

    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                4097: ZontDigitalBusAdapterData(
                    4097,
                    6,
                    "Navien",
                    flow_temperature=35,
                )
            }
        ),
    )
    assert entity.available

    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                4097: ZontDigitalBusAdapterData(
                    4097,
                    6,
                    "Navien",
                    available=False,
                    flow_temperature=35,
                )
            }
        ),
    )
    assert not entity.available


async def test_setup_adds_new_fields_without_duplicates(hass) -> None:
    adapter = ZontDigitalBusAdapterData(
        object_id=4097,
        object_type=6,
        name="Navien",
        flow_temperature=35,
        dhw_temperature=29,
        state=ZontDigitalBusState.OFF,
        error_code=0,
    )
    entry = _entry(ZontControllerData(info=None), {4097: adapter})
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert len(async_add_entities.call_args_list[0].args[0]) == 2
    first_adapter_entities = async_add_entities.call_args_list[1].args[0]
    assert {entity.entity_description.key for entity in first_adapter_entities} == {
        "flow_temperature",
        "dhw_temperature",
        "state",
        "error_code",
    }

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                4097: ZontDigitalBusAdapterData(
                    object_id=4097,
                    object_type=6,
                    name="Navien",
                    flow_temperature=35,
                    dhw_temperature=29,
                    return_temperature=30,
                    modulation=50,
                    pressure=2.1,
                    state=ZontDigitalBusState.RUNNING,
                    error_code=0,
                )
            }
        ),
    )
    listener()

    new_entities = async_add_entities.call_args_list[2].args[0]
    assert {entity.entity_description.key for entity in new_entities} == {
        "return_temperature",
        "modulation",
        "pressure",
    }

    listener()
    assert async_add_entities.call_count == 3


def test_digital_temperature_sensor_uses_native_measurement() -> None:
    sensor_data = ZontDigitalTemperatureSensorData(
        object_id=8196,
        object_type=1,
        name="Погода из интернета",
        temperature=19.7,
    )
    entry = _entry(ZontControllerData(info=None), {8196: sensor_data})

    entity = ZontDigitalTemperatureSensor(entry, 8196)

    assert entity.available
    assert entity.native_value == 19.7
    assert entity.device_class is SensorDeviceClass.TEMPERATURE
    assert entity.native_unit_of_measurement == "°C"
    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.suggested_display_precision == 1
    assert entity.unique_id == "ABCDEF123456_8196_temperature"
    assert entity.suggested_object_id == "temperature"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:8196")}


def test_digital_temperature_sensor_tracks_availability() -> None:
    sensor_data = ZontDigitalTemperatureSensorData(
        object_id=8196,
        object_type=1,
        name="Погода из интернета",
        available=False,
        temperature=19.7,
    )
    entry = _entry(ZontControllerData(info=None), {8196: sensor_data})
    entity = ZontDigitalTemperatureSensor(entry, 8196)

    assert not entity.available
    assert entity.native_value == 19.7

    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                8196: ZontDigitalTemperatureSensorData(
                    8196,
                    1,
                    "Погода из интернета",
                    temperature=None,
                )
            }
        ),
    )
    assert not entity.available


async def test_setup_adds_unavailable_temperature_sensor_without_duplicates(
    hass,
) -> None:
    sensor_data = ZontDigitalTemperatureSensorData(
        object_id=8196,
        object_type=1,
        name="Погода из интернета",
        available=False,
    )
    entry = _entry(ZontControllerData(info=None), {8196: sensor_data})
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontDigitalTemperatureSensor)
    assert not entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2


async def test_setup_adds_analog_value_without_waiting_for_current_value(
    hass,
) -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Контроль напряжения питания",
        available=False,
        subtype=0,
        unit_code=0,
    )
    entry = _entry(ZontControllerData(info=None), {20550: analog_input})
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontAnalogInputValueSensor)
    assert not entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2
