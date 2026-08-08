"""Tests for typed ZONT object models."""

from __future__ import annotations

import pytest
from custom_components.zont_ws.objects import (
    ANALOG_INPUT_SUBTYPE_NAMES,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
    OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
    OBJECT_TYPE_NTC_TEMPERATURE_SENSOR,
    OBJECT_TYPE_RADIO_SENSOR,
    RADIO_SENSOR_SUBTYPE_NAMES,
    SUPPORTED_RADIO_SENSOR_SUBTYPES,
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontNtcTemperatureSensorData,
    ZontObjectParseError,
    ZontRadioSensorData,
    analog_input_model,
    immutable_objects,
    parse_analog_input,
    parse_digital_bus_adapter,
    parse_digital_temperature_sensor,
    parse_ntc_temperature_sensor,
    parse_radio_sensor,
    parse_zont_object,
    radio_sensor_model,
    unavailable_object,
)


def test_parse_complete_analog_input() -> None:
    analog_input = parse_analog_input(
        {
            "id": 20550,
            "type": OBJECT_TYPE_ANALOG_INPUT,
            "stype": 0,
            "name": "Контроль напряжения питания",
            "v": 12.2,
            "u": 0,
            "trig": 0,
            "a": 1,
        }
    )

    assert analog_input == ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Контроль напряжения питания",
        available=True,
        subtype=0,
        value=12.2,
        unit_code=0,
        triggered=False,
    )


def test_all_documented_analog_input_subtypes_have_models() -> None:
    assert ANALOG_INPUT_SUBTYPE_NAMES == {
        0: "Аналоговый вход без пресета",
        1: "Датчик давления 5 бар",
        2: "Датчик давления 12 бар",
        3: "Датчик открытия двери",
        4: "ИК-датчик движения с контролем шлейфа",
        5: "Датчик дыма",
        6: "Датчик протечки",
        7: "ИК-датчик движения без контроля шлейфа",
        8: "Комнатный термостат",
        9: "Авария котла +",
        10: "Авария котла -",
        11: "Вход «Зажигание»",
        12: "Датчик скорости",
        13: "Датчик оборотов двигателя",
        14: "Дискретный вход",
        15: "Тревожная кнопка",
        16: "Датчик расхода топлива",
        17: "Датчик влажности",
        18: "Датчик давления 6 бар",
        19: "Дискретный вход НР",
        20: "Дискретный вход НЗ",
        21: "Датчик давления 10 бар",
    }
    assert analog_input_model(22) == "Аналоговый вход (подтип 22)"


def test_partial_analog_input_update_preserves_absent_fields() -> None:
    previous = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Контроль напряжения питания",
        subtype=0,
        value=12.2,
        unit_code=0,
        triggered=False,
    )

    analog_input = parse_analog_input(
        {"id": 20550, "trig": 1},
        previous,
        partial=True,
    )

    assert analog_input.available
    assert analog_input.value == 12.2
    assert analog_input.unit_code == 0
    assert analog_input.triggered is True


def test_unavailable_analog_input_preserves_last_state() -> None:
    previous = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Контроль напряжения питания",
        subtype=0,
        value=12.2,
        unit_code=0,
        triggered=False,
    )

    analog_input = parse_analog_input(
        {
            "id": 20550,
            "type": 0,
            "stype": 0,
            "name": "Контроль напряжения питания",
            "a": 0,
        },
        previous,
    )

    assert not analog_input.available
    assert analog_input.value == 12.2
    assert analog_input.unit_code == 0
    assert analog_input.triggered is False


@pytest.mark.parametrize(
    ("payload", "available", "value", "triggered"),
    [
        ({"v": 1.0, "trig": 0, "a": 1}, True, 1.0, False),
        ({"v": 1.0, "trig": 1, "a": 0}, False, 1.0, True),
        ({"v": 1.0, "trig": 0}, True, 1.0, False),
        ({"a": 1}, True, None, None),
        ({}, False, None, None),
        ({"v": True, "trig": 2, "a": 1}, True, None, None),
        ({"v": float("nan"), "trig": "0", "a": "1"}, False, None, None),
    ],
)
def test_analog_input_values_and_availability(
    payload: dict[str, object],
    available: bool,
    value: float | None,
    triggered: bool | None,
) -> None:
    analog_input = parse_analog_input(
        {
            "id": 20550,
            "type": 0,
            "stype": 0,
            "name": "Вход",
            **payload,
        }
    )

    assert analog_input.available is available
    assert analog_input.value == value
    assert analog_input.triggered is triggered


def test_analog_input_requires_non_negative_subtype() -> None:
    with pytest.raises(ZontObjectParseError):
        parse_analog_input({"id": 20550, "type": 0, "stype": -1, "name": "Вход"})


def test_parse_complete_digital_bus_adapter() -> None:
    adapter = parse_digital_bus_adapter(
        {
            "id": 4097,
            "type": OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
            "name": "Navien",
            "water": 45.6,
            "dhw": 34.5,
            "return": 30.4,
            "modul": 99,
            "press": 2.4,
            "state": 1,
            "err": 0,
        }
    )

    assert adapter == ZontDigitalBusAdapterData(
        object_id=4097,
        object_type=6,
        name="Navien",
        flow_temperature=45.6,
        dhw_temperature=34.5,
        return_temperature=30.4,
        modulation=99.0,
        pressure=2.4,
        state=ZontDigitalBusState.RUNNING,
        error_code=0,
    )


def test_partial_update_preserves_absent_fields() -> None:
    previous = parse_digital_bus_adapter(
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": 35,
            "dhw": 29,
            "state": 0,
            "err": 0,
        }
    )

    adapter = parse_digital_bus_adapter(
        {"id": 4097, "water": 36.5},
        previous,
        partial=True,
    )

    assert adapter.flow_temperature == 36.5
    assert adapter.dhw_temperature == 29.0
    assert adapter.state is ZontDigitalBusState.OFF
    assert adapter.error_code == 0


def test_invalid_optional_fields_do_not_break_adapter() -> None:
    adapter = parse_digital_bus_adapter(
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": True,
            "dhw": float("nan"),
            "state": [],
            "err": 1.5,
        }
    )

    assert adapter.flow_temperature is None
    assert adapter.dhw_temperature is None
    assert adapter.state is None
    assert adapter.error_code is None


@pytest.mark.parametrize(
    "payload",
    [
        {"type": 6, "name": "Navien"},
        {"id": 4097, "type": 1, "name": "Navien"},
        {"id": 4097, "type": 6, "name": ""},
    ],
)
def test_invalid_identity_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ZontObjectParseError):
        parse_digital_bus_adapter(payload)


def test_object_registry_and_unavailable_copy_are_immutable() -> None:
    adapter = ZontDigitalBusAdapterData(4097, 6, "Navien")
    objects = immutable_objects({4097: adapter})

    assert not unavailable_object(adapter).available
    with pytest.raises(TypeError):
        objects[4098] = adapter  # type: ignore[index]


def test_parse_complete_digital_temperature_sensor() -> None:
    sensor = parse_digital_temperature_sensor(
        {
            "id": 8196,
            "type": OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
            "name": "Погода из интернета",
            "t": 19.7,
            "a": 1,
            "trig": 0,
        }
    )

    assert sensor == ZontDigitalTemperatureSensorData(
        object_id=8196,
        object_type=1,
        name="Погода из интернета",
        available=True,
        temperature=19.7,
    )


def test_parse_complete_ntc_temperature_sensor() -> None:
    sensor = parse_ntc_temperature_sensor(
        {
            "id": 20487,
            "type": OBJECT_TYPE_NTC_TEMPERATURE_SENSOR,
            "name": "Температура котла",
            "t": 45.6,
            "a": 1,
        }
    )

    assert sensor == ZontNtcTemperatureSensorData(
        object_id=20487,
        object_type=27,
        name="Температура котла",
        available=True,
        temperature=45.6,
    )


@pytest.mark.parametrize(
    ("payload", "available", "temperature"),
    [
        ({"t": 45.6, "a": 1}, True, 45.6),
        ({"t": 45.6, "a": 0}, False, 45.6),
        ({"t": 45.6}, True, 45.6),
        ({"a": 1}, True, None),
        ({}, False, None),
        ({"t": True, "a": 1}, True, None),
        ({"t": float("inf"), "a": 1}, True, None),
        ({"t": 45.6, "a": "1"}, False, 45.6),
    ],
)
def test_ntc_temperature_value_and_availability(
    payload: dict[str, object],
    available: bool,
    temperature: float | None,
) -> None:
    sensor = parse_ntc_temperature_sensor(
        {
            "id": 20487,
            "type": 27,
            "name": "Температура котла",
            **payload,
        }
    )

    assert sensor.available is available
    assert sensor.temperature == temperature


def test_partial_ntc_update_preserves_absent_fields_and_availability() -> None:
    previous = ZontNtcTemperatureSensorData(
        object_id=20487,
        object_type=27,
        name="Температура котла",
        available=False,
        temperature=45.6,
    )

    sensor = parse_ntc_temperature_sensor(
        {"id": 20487, "t": 46.1},
        previous,
        partial=True,
    )

    assert not sensor.available
    assert sensor.name == "Температура котла"
    assert sensor.temperature == 46.1


def test_unavailable_ntc_update_preserves_last_temperature() -> None:
    previous = ZontNtcTemperatureSensorData(
        object_id=20487,
        object_type=27,
        name="Температура котла",
        temperature=45.6,
    )

    sensor = parse_ntc_temperature_sensor(
        {
            "id": 20487,
            "type": 27,
            "name": "Температура котла",
            "a": 0,
        },
        previous,
    )

    assert not sensor.available
    assert sensor.temperature == 45.6


def test_ntc_parser_rejects_wrong_object_type() -> None:
    with pytest.raises(ZontObjectParseError):
        parse_ntc_temperature_sensor(
            {"id": 20487, "type": 1, "name": "Температура котла"}
        )


@pytest.mark.parametrize(
    ("payload", "available", "temperature"),
    [
        ({"t": 19.7, "a": 1}, True, 19.7),
        ({"t": 19.7, "a": 0}, False, 19.7),
        ({"t": 19.7}, True, 19.7),
        ({"a": 1}, True, None),
        ({"t": True, "a": 1}, True, None),
        ({"t": float("nan"), "a": 1}, True, None),
        ({"t": 19.7, "a": "1"}, False, 19.7),
    ],
)
def test_temperature_sensor_value_and_availability(
    payload: dict[str, object],
    available: bool,
    temperature: float | None,
) -> None:
    sensor = parse_digital_temperature_sensor(
        {
            "id": 8196,
            "type": 1,
            "name": "Погода из интернета",
            **payload,
        }
    )

    assert sensor.available is available
    assert sensor.temperature == temperature


def test_partial_temperature_update_preserves_availability() -> None:
    previous = ZontDigitalTemperatureSensorData(
        object_id=8196,
        object_type=1,
        name="Погода из интернета",
        available=False,
        temperature=19.7,
    )

    sensor = parse_digital_temperature_sensor(
        {"id": 8196, "t": 20.1},
        previous,
        partial=True,
    )

    assert not sensor.available
    assert sensor.temperature == 20.1


def test_unavailable_full_update_preserves_last_temperature() -> None:
    previous = ZontDigitalTemperatureSensorData(
        object_id=8196,
        object_type=1,
        name="Погода из интернета",
        temperature=19.7,
    )

    sensor = parse_digital_temperature_sensor(
        {
            "id": 8196,
            "type": 1,
            "name": "Погода из интернета",
            "a": 0,
        },
        previous,
    )

    assert not sensor.available
    assert sensor.temperature == 19.7


def test_radio_sensor_subtypes_and_public_support_are_explicit() -> None:
    assert set(RADIO_SENSOR_SUBTYPE_NAMES) == {
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        23,
    }
    assert {5, 10, 11, 15, 18} == SUPPORTED_RADIO_SENSOR_SUBTYPES
    assert radio_sensor_model(18) == "Радиодатчик температуры и влажности"
    assert radio_sensor_model(99) == "Радиодатчик (подтип 99)"


def test_parse_complete_radio_temperature_and_humidity_sensor() -> None:
    sensor = parse_radio_sensor(
        {
            "id": 12001,
            "type": OBJECT_TYPE_RADIO_SENSOR,
            "stype": 18,
            "name": "Гостиная",
            "t": 23.4,
            "h": 48,
            "b": 2.91,
            "r": 86,
            "trig": 0,
            "a": 1,
        }
    )

    assert sensor == ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Гостиная",
        available=True,
        subtype=18,
        temperature=23.4,
        humidity=48.0,
        battery_voltage=2.91,
        signal_strength_raw=86.0,
        triggered=False,
    )


def test_radio_sensor_rejects_invalid_state_values_without_failing() -> None:
    sensor = parse_radio_sensor(
        {
            "id": 12001,
            "type": 8,
            "stype": 18,
            "name": "Гостиная",
            "t": True,
            "h": float("nan"),
            "b": float("inf"),
            "r": "86",
            "trig": 2,
            "a": 1,
        }
    )

    assert sensor.available
    assert sensor.temperature is None
    assert sensor.humidity is None
    assert sensor.battery_voltage is None
    assert sensor.signal_strength_raw is None
    assert sensor.triggered is None


def test_partial_radio_update_preserves_absent_fields() -> None:
    previous = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Гостиная",
        subtype=18,
        temperature=23.4,
        humidity=48,
        battery_voltage=2.91,
        signal_strength_raw=86,
        triggered=False,
    )

    sensor = parse_radio_sensor(
        {"id": 12001, "h": 49, "trig": 1},
        previous,
        partial=True,
    )

    assert sensor.temperature == 23.4
    assert sensor.humidity == 49.0
    assert sensor.battery_voltage == 2.91
    assert sensor.signal_strength_raw == 86
    assert sensor.triggered is True


def test_unavailable_radio_update_preserves_all_last_values() -> None:
    previous = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Гостиная",
        subtype=18,
        temperature=23.4,
        humidity=48,
        battery_voltage=2.91,
        signal_strength_raw=86,
        triggered=False,
    )

    sensor = parse_radio_sensor(
        {
            "id": 12001,
            "type": 8,
            "stype": 18,
            "name": "Гостиная",
            "t": 99,
            "h": 99,
            "b": 1,
            "r": 1,
            "trig": 1,
            "a": 0,
        },
        previous,
    )

    assert not sensor.available
    assert sensor.temperature == 23.4
    assert sensor.humidity == 48
    assert sensor.battery_voltage == 2.91
    assert sensor.signal_strength_raw == 86
    assert sensor.triggered is False


def test_unknown_radio_subtype_is_parsed_for_future_support() -> None:
    sensor = parse_zont_object(
        {
            "id": 12002,
            "type": 8,
            "stype": 99,
            "name": "Будущий радиодатчик",
            "b": 2.8,
        }
    )

    assert isinstance(sensor, ZontRadioSensorData)
    assert sensor.subtype == 99
    assert sensor.available


def test_generic_parser_dispatches_using_previous_object_type() -> None:
    previous = ZontDigitalTemperatureSensorData(
        object_id=8196,
        object_type=1,
        name="Погода из интернета",
        temperature=19.7,
    )

    sensor = parse_zont_object(
        {"id": 8196, "t": 20.1},
        previous,
        partial=True,
    )

    assert isinstance(sensor, ZontDigitalTemperatureSensorData)
    assert sensor.temperature == 20.1


def test_generic_parser_dispatches_ntc_using_previous_object_type() -> None:
    previous = ZontNtcTemperatureSensorData(
        object_id=20487,
        object_type=27,
        name="Температура котла",
        temperature=45.6,
    )

    sensor = parse_zont_object(
        {"id": 20487, "t": 46.1},
        previous,
        partial=True,
    )

    assert isinstance(sensor, ZontNtcTemperatureSensorData)
    assert sensor.temperature == 46.1


def test_generic_parser_dispatches_radio_using_previous_object_type() -> None:
    previous = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Гостиная",
        subtype=18,
        temperature=23.4,
        humidity=48,
    )

    sensor = parse_zont_object(
        {"id": 12001, "h": 49},
        previous,
        partial=True,
    )

    assert isinstance(sensor, ZontRadioSensorData)
    assert sensor.temperature == 23.4
    assert sensor.humidity == 49


def test_generic_parser_rejects_unknown_object_type() -> None:
    with pytest.raises(ZontObjectParseError):
        parse_zont_object({"id": 1, "type": 99, "name": "Объект"})
