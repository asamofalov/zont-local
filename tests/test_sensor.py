"""Tests for ZONT controller sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.coordinator import (
    ZontDataUpdateCoordinator,
)
from custom_components.zont_ws.data import ZontControllerData, ZontData
from custom_components.zont_ws.entities.analog_input import (
    ANALOG_INPUT_UNITS,
    ZontAnalogInputValueSensor,
)
from custom_components.zont_ws.entities.controller import (
    CONNECTION_CHANNEL_STATES,
    ZontConnectionChannelSensor,
    ZontGsmRegistrationSensor,
    ZontGsmSignalSensor,
    ZontPowerSourceSensor,
    ZontSupplyVoltageSensor,
    ZontWifiSignalSensor,
)
from custom_components.zont_ws.entities.digital_bus import (
    DIGITAL_BUS_SENSOR_DESCRIPTIONS,
    ZontDigitalBusSensor,
)
from custom_components.zont_ws.entities.heating.states import (
    CONSUMER_CONTROL_MODE_STATES,
    ZontHeatingCalculatedWaterTemperatureSensor,
    ZontHeatingControlModeSensor,
)
from custom_components.zont_ws.entities.mixer import ZontMixerStateSensor
from custom_components.zont_ws.entities.radio import (
    RADIO_SENSOR_DESCRIPTIONS,
    ZontRadioSensor,
)
from custom_components.zont_ws.entities.temperature import (
    ZontDigitalTemperatureSensor,
    ZontNtcTemperatureSensor,
)
from custom_components.zont_ws.protocol import ZontClient
from custom_components.zont_ws.protocol.controller import (
    ZontCommunicationChannel,
    ZontGsmRegistrationState,
    ZontGsmStatus,
    ZontPowerSource,
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
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontMixerDirection,
    ZontNtcTemperatureSensorData,
    ZontObject,
    ZontRadioSensorData,
    immutable_objects,
)
from custom_components.zont_ws.runtime import ZontRuntimeData
from custom_components.zont_ws.sensor import async_setup_entry
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(
    controller: ZontControllerData,
    objects: dict[int, ZontObject] | None = None,
    controls: dict[int, ZontHeatingCircuitControlData] | None = None,
    heating_states: dict[int, ZontHeatingCircuitInternalState] | None = None,
    mixer_states: dict[int, ZontMixerInternalState] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=controller,
        objects=immutable_objects(objects),
        heating_controls=immutable_heating_controls(controls),
        heating_states=immutable_heating_states(heating_states),
        mixer_states=immutable_mixer_states(mixer_states),
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


def test_extended_controller_sensors_use_safe_derived_values() -> None:
    """Expose only safe status and normalized signal values."""
    entry = _entry(
        ZontControllerData(
            info=None,
            power_source=ZontPowerSource.MAIN,
            wifi_status=ZontWifiStatus(connected=True, raw_signal=86),
            gsm_status=ZontGsmStatus(ZontGsmRegistrationState.SEARCHING, 0),
        )
    )

    power = ZontPowerSourceSensor(entry)
    registration = ZontGsmRegistrationSensor(entry)
    wifi_signal = ZontWifiSignalSensor(entry)
    gsm_signal = ZontGsmSignalSensor(entry)

    assert power.native_value == "main"
    assert power.options == ["main", "battery"]
    assert registration.native_value == "searching"
    assert wifi_signal.native_value == 28
    assert gsm_signal.native_value == 0
    assert wifi_signal.native_unit_of_measurement == PERCENTAGE
    assert gsm_signal.native_unit_of_measurement == PERCENTAGE
    assert not wifi_signal.entity_registry_enabled_default
    assert not gsm_signal.entity_registry_enabled_default
    assert all(
        entity.entity_category is EntityCategory.DIAGNOSTIC
        for entity in (power, registration, wifi_signal, gsm_signal)
    )
    assert wifi_signal.unique_id == "ABCDEF123456_wifi_signal"
    assert gsm_signal.unique_id == "ABCDEF123456_gsm_signal"


def test_gsm_signal_is_unavailable_for_unknown_raw_level() -> None:
    """Keep GSM registration usable without inventing a signal percentage."""
    entry = _entry(
        ZontControllerData(
            info=None,
            gsm_status=ZontGsmStatus(ZontGsmRegistrationState.UNKNOWN, None),
        )
    )

    assert ZontGsmRegistrationSensor(entry).available
    assert not ZontGsmSignalSensor(entry).available


async def test_setup_adds_supported_controller_sensors_once(hass) -> None:
    """Add optional controller sensors after a supported response without duplicates."""
    entry = _entry(
        ZontControllerData(
            info=None,
            power_source=ZontPowerSource.MAIN,
            wifi_status=ZontWifiStatus(connected=True, raw_signal=86),
            gsm_status=ZontGsmStatus(ZontGsmRegistrationState.SEARCHING, 0),
        )
    )
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    optional_entities = async_add_entities.call_args_list[1].args[0]
    assert {type(entity) for entity in optional_entities} == {
        ZontPowerSourceSensor,
        ZontGsmRegistrationSensor,
        ZontWifiSignalSensor,
        ZontGsmSignalSensor,
    }
    controller_listener = (
        entry.runtime_data.coordinator.async_add_listener.call_args_list[0].args[0]
    )
    controller_listener()
    assert async_add_entities.call_count == 2


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


def test_ntc_temperature_sensor_uses_native_measurement() -> None:
    sensor_data = ZontNtcTemperatureSensorData(
        object_id=20487,
        object_type=27,
        name="Температура котла",
        temperature=45.6,
    )
    entry = _entry(ZontControllerData(info=None), {20487: sensor_data})

    entity = ZontNtcTemperatureSensor(entry, 20487)

    assert entity.available
    assert entity.native_value == 45.6
    assert entity.device_class is SensorDeviceClass.TEMPERATURE
    assert entity.native_unit_of_measurement == "°C"
    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.suggested_display_precision == 1
    assert entity.unique_id == "ABCDEF123456_20487_temperature"
    assert entity.suggested_object_id == "temperature"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:20487")}


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


def test_radio_sensor_entities_map_measurements_and_diagnostics() -> None:
    sensor_data = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Гостиная",
        subtype=18,
        temperature=23.4,
        humidity=48,
        battery_voltage=2.91,
        signal_strength_raw=86,
    )
    entry = _entry(ZontControllerData(info=None), {12001: sensor_data})
    entities = {
        key: ZontRadioSensor(entry, 12001, description)
        for key, description in RADIO_SENSOR_DESCRIPTIONS.items()
    }

    assert entities["temperature"].native_value == 23.4
    assert entities["temperature"].device_class is SensorDeviceClass.TEMPERATURE
    assert entities["temperature"].native_unit_of_measurement == "°C"
    assert entities["humidity"].native_value == 48
    assert entities["humidity"].device_class is SensorDeviceClass.HUMIDITY
    assert entities["humidity"].native_unit_of_measurement == PERCENTAGE
    assert entities["battery_voltage"].native_value == 2.91
    assert entities["battery_voltage"].device_class is SensorDeviceClass.VOLTAGE
    assert entities["battery_voltage"].native_unit_of_measurement == (
        UnitOfElectricPotential.VOLT
    )
    assert entities["battery_voltage"].entity_category is EntityCategory.DIAGNOSTIC
    assert entities["signal_strength"].native_value == -30
    assert entities["signal_strength"].device_class is (
        SensorDeviceClass.SIGNAL_STRENGTH
    )
    assert entities["signal_strength"].native_unit_of_measurement == (
        SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    )
    assert entities["signal_strength"].entity_category is EntityCategory.DIAGNOSTIC
    assert entities["signal_strength"].unique_id == (
        "ABCDEF123456_12001_signal_strength"
    )
    assert entities["signal_strength"].suggested_object_id == "signal_strength"


async def test_setup_adds_fixed_radio_sensor_matrix_without_duplicates(hass) -> None:
    sensor_data = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Гостиная",
        available=False,
        subtype=18,
        temperature=23.4,
    )
    entry = _entry(ZontControllerData(info=None), {12001: sensor_data})
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert {entity.entity_description.key for entity in entities} == {
        "temperature",
        "humidity",
        "battery_voltage",
        "signal_strength",
    }
    assert all(not entity.available for entity in entities)

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2


@pytest.mark.parametrize(
    ("subtype", "fields"),
    [
        (5, {"temperature", "battery_voltage", "signal_strength"}),
        (10, {"battery_voltage", "signal_strength"}),
        (11, {"battery_voltage", "signal_strength"}),
        (15, {"temperature", "battery_voltage", "signal_strength"}),
        (
            18,
            {"temperature", "humidity", "battery_voltage", "signal_strength"},
        ),
        (23, set()),
        (99, set()),
    ],
)
async def test_setup_uses_supported_radio_sensor_matrix(
    hass,
    subtype: int,
    fields: set[str],
) -> None:
    sensor_data = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Радиодатчик",
        subtype=subtype,
    )
    entry = _entry(ZontControllerData(info=None), {12001: sensor_data})
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    if not fields:
        assert async_add_entities.call_count == 1
        return
    entities = async_add_entities.call_args_list[1].args[0]
    assert {entity.entity_description.key for entity in entities} == fields


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


async def test_setup_adds_unavailable_ntc_sensor_without_duplicates(hass) -> None:
    sensor_data = ZontNtcTemperatureSensorData(
        object_id=20487,
        object_type=27,
        name="Температура котла",
        available=False,
        temperature=45.6,
    )
    entry = _entry(ZontControllerData(info=None))
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert async_add_entities.call_count == 1
    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({20487: sensor_data}),
    )
    listener()

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontNtcTemperatureSensor)
    assert not entities[0].available
    assert entities[0].native_value == 45.6

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


def test_heating_control_mode_sensor_uses_resolved_enum() -> None:
    circuit = ZontHeatingCircuitData(
        object_id=9825,
        object_type=16,
        name="Кабинет",
        subtype=3,
    )
    control = ZontHeatingCircuitControlData(
        control_mode=ZontConsumerControlMode.AIR_PID,
        has_weather_compensation=True,
        target_sensor_id=4110,
        min_temperature=10,
        max_temperature=40,
    )
    entry = _entry(
        ZontControllerData(info=None),
        {9825: circuit},
        {9825: control},
    )

    entity = ZontHeatingControlModeSensor(entry, 9825)

    assert entity.available
    assert entity.native_value == "air_pid"
    assert entity.options == list(CONSUMER_CONTROL_MODE_STATES)
    assert entity.device_class is SensorDeviceClass.ENUM
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.entity_registry_enabled_default
    assert entity.unique_id == "ABCDEF123456_9825_control_mode"
    assert entity.suggested_object_id == "control_mode"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:9825")}


@pytest.mark.parametrize(
    ("temperature", "available"),
    [(47.0, True), (35.0, True), (None, False)],
)
def test_calculated_water_temperature_sensor_uses_internal_state(
    temperature: float | None,
    available: bool,
) -> None:
    circuit = ZontHeatingCircuitData(
        object_id=9825,
        object_type=16,
        name="Кабинет",
        subtype=3,
    )
    state = ZontHeatingCircuitInternalState(
        object_id=9825,
        target_sensor_id=4110,
        status_register=0,
        calculated_water_temperature=temperature,
    )
    entry = _entry(
        ZontControllerData(info=None),
        {9825: circuit},
        heating_states={9825: state},
    )

    entity = ZontHeatingCalculatedWaterTemperatureSensor(entry, 9825)

    assert entity.available is available
    assert entity.native_value == temperature
    assert entity.device_class is SensorDeviceClass.TEMPERATURE
    assert entity.native_unit_of_measurement is UnitOfTemperature.CELSIUS
    assert entity.state_class is None
    assert entity.entity_category is None
    assert entity.unique_id == "ABCDEF123456_9825_calculated_water_temperature"
    assert entity.suggested_object_id == "calculated_water_temperature"


async def test_setup_adds_control_mode_only_for_consumer_circuit(hass) -> None:
    consumer = ZontHeatingCircuitData(9825, 16, "Кабинет", subtype=3)
    entry = _entry(ZontControllerData(info=None), {9825: consumer})
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert {type(entity) for entity in entities} == {
        ZontHeatingControlModeSensor,
        ZontHeatingCalculatedWaterTemperatureSensor,
    }
    assert all(not entity.available for entity in entities)

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2

    dhw = ZontHeatingCircuitData(8362, 16, "ГВС", subtype=1)
    dhw_entry = _entry(ZontControllerData(info=None), {8362: dhw})
    dhw_entry.add_to_hass(hass)
    dhw_add_entities = MagicMock()

    await async_setup_entry(hass, dhw_entry, dhw_add_entities)

    assert dhw_add_entities.call_count == 1


@pytest.mark.parametrize(
    ("direction", "flags", "expected", "available"),
    [
        (ZontMixerDirection.OPENING, None, "opening", True),
        (ZontMixerDirection.CLOSING, None, "closing", True),
        (ZontMixerDirection.IDLE, 1, "fully_open", True),
        (ZontMixerDirection.IDLE, 2, "fully_closed", True),
        (ZontMixerDirection.IDLE, 0, "idle", True),
        (ZontMixerDirection.IDLE, 3, None, False),
        (ZontMixerDirection.IDLE, None, None, False),
    ],
)
def test_mixer_state_sensor_resolves_movement_and_position(
    direction: ZontMixerDirection,
    flags: int | None,
    expected: str | None,
    available: bool,
) -> None:
    mixer = ZontMixerData(9078, 15, "Трехходовой ТП", direction=direction)
    states = (
        {
            9078: ZontMixerInternalState(
                object_id=9078,
                direction=ZontMixerDirection.IDLE,
                state_flags=flags,
            )
        }
        if flags is not None
        else None
    )
    entry = _entry(ZontControllerData(info=None), {9078: mixer}, mixer_states=states)

    entity = ZontMixerStateSensor(entry, 9078)

    assert entity.available is available
    assert entity.native_value == expected
    assert entity.options == [
        "idle",
        "opening",
        "closing",
        "fully_open",
        "fully_closed",
    ]
    assert entity.device_class is SensorDeviceClass.ENUM
    assert entity.unique_id == "ABCDEF123456_9078_state"
    assert entity.suggested_object_id is None
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:9078")}


async def test_setup_adds_mixer_state_without_duplicates(hass) -> None:
    mixer = ZontMixerData(
        9078,
        15,
        "Трехходовой ТП",
        direction=ZontMixerDirection.IDLE,
    )
    entry = _entry(ZontControllerData(info=None), {9078: mixer})
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontMixerStateSensor)
    assert not entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2
