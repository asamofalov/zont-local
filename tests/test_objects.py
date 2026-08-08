"""Tests for typed ZONT object models."""

from __future__ import annotations

import pytest
from custom_components.zont_ws.objects import (
    ANALOG_INPUT_SUBTYPE_NAMES,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
    OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontObjectParseError,
    analog_input_model,
    immutable_objects,
    parse_analog_input,
    parse_digital_bus_adapter,
    parse_digital_temperature_sensor,
    parse_zont_object,
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


def test_generic_parser_rejects_unknown_object_type() -> None:
    with pytest.raises(ZontObjectParseError):
        parse_zont_object({"id": 1, "type": 8, "name": "Радиодатчик"})
