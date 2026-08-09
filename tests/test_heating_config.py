"""Tests for internal ZONT heating configuration metadata."""

from __future__ import annotations

import pytest
from custom_components.zont_ws.heating_config import (
    AIR_MAX_TEMPERATURE,
    AIR_MIN_TEMPERATURE,
    SLAVE_MODE_FLAG,
    WATER_MAX_TEMPERATURE,
    WATER_MIN_TEMPERATURE,
    WEATHER_COMPENSATION_MAX_TEMPERATURE,
    WEATHER_COMPENSATION_MIN_TEMPERATURE,
    WEATHER_COMPENSATION_REQUEST_ONLY_FLAG,
    ZontConsumerControlMode,
    ZontHeatingCircuitConfiguration,
    ZontHeatingCircuitInternalState,
    ZontHeatingConfigParseError,
    ZontHeatingModeConfiguration,
    ZontTemperatureSensorConfiguration,
    immutable_heating_controls,
    immutable_heating_modes,
    immutable_heating_states,
    parse_heating_circuit_configuration,
    parse_heating_circuit_internal_state,
    parse_heating_mode_configuration,
    parse_temperature_sensor_configuration,
    resolve_heating_circuit_control,
)


def _configuration(
    *,
    setting_register: int = 2,
    water_min_temperature: float | None = 41,
    water_max_temperature: float | None = 80,
    external_thermostat_id: int | None = None,
    pza: int | None = None,
) -> ZontHeatingCircuitConfiguration:
    return ZontHeatingCircuitConfiguration(
        object_id=20496,
        name="Радиаторы",
        subtype=3,
        water_min_temperature=water_min_temperature,
        water_max_temperature=water_max_temperature,
        air_temperature_sensor_id=None,
        air_temperature_reserve_sensor_id=None,
        water_temperature_sensor_id=4104,
        water_temperature_reserve_sensor_id=None,
        setting_register=setting_register,
        external_thermostat_id=external_thermostat_id,
        pza=pza,
        heat_source_id=None,
    )


def test_parse_heating_circuit_configuration_with_nested_fields() -> None:
    configuration = parse_heating_circuit_configuration(
        "#Z20496:16,'Радиаторы, первый этаж',3,3140,3530,0,0,4104,0,20,"
        "[9044,[1,2]],2562,0,0,2730,0,0,0,0,0,0,0,3230,100,10,0,0,0,0,0",
        20496,
    )

    assert configuration.object_id == 20496
    assert configuration.name == "Радиаторы, первый этаж"
    assert configuration.subtype == 3
    assert configuration.water_min_temperature == 41
    assert configuration.water_max_temperature == 80
    assert configuration.water_temperature_sensor_id == 4104
    assert configuration.air_temperature_sensor_id is None
    assert configuration.setting_register == 2562
    assert configuration.external_thermostat_id is None
    assert configuration.pza == 0


def test_parse_real_weather_compensation_configuration() -> None:
    configuration = parse_heating_circuit_configuration(
        "#Z9171:16,'ТП',3,3090,3230,0,0,4103,0,20,[9055,9078],"
        "133634,0,0,2780,9641,0,0,0,0,0,0,3230,100,10,0,0,0,0,0"
    )

    assert configuration.water_min_temperature == 36
    assert configuration.water_max_temperature == 50
    assert configuration.has_weather_compensation
    control = resolve_heating_circuit_control(configuration, 4103)
    assert control.control_mode is ZontConsumerControlMode.WATER
    assert control.has_weather_compensation
    assert control.min_temperature == WEATHER_COMPENSATION_MIN_TEMPERATURE
    assert control.max_temperature == WEATHER_COMPENSATION_MAX_TEMPERATURE


def test_parse_internal_state_target_sensor() -> None:
    state = parse_heating_circuit_internal_state(
        "#Y9171$3090,2980,[],0,0,20501,4103,1,[20501,20504],20504,0",
        9171,
    )

    assert state.object_id == 9171
    assert state.target_sensor_id == 4103
    assert state.status_register == 1
    assert state.applicable_mode_ids == (20501, 20504)
    assert state.is_blocked is False
    assert state.has_sensor_fault is False
    assert state.is_summer_mode is False


@pytest.mark.parametrize(
    ("status_register", "blocked", "sensor_fault", "summer_mode"),
    [
        (0, False, False, False),
        (2, True, False, False),
        (8, False, True, False),
        (128, False, False, True),
        (138, True, True, True),
    ],
)
def test_parse_internal_state_status_flags(
    status_register: int,
    blocked: bool,
    sensor_fault: bool,
    summer_mode: bool,
) -> None:
    state = parse_heating_circuit_internal_state(
        f"#Y20496$3160,3140,[],0,0,0,4104,{status_register}"
    )

    assert state.status_register == status_register
    assert state.is_blocked is blocked
    assert state.has_sensor_fault is sensor_fault
    assert state.is_summer_mode is summer_mode


def test_short_internal_state_keeps_control_data_without_status() -> None:
    state = parse_heating_circuit_internal_state("#Y20496$3160,3140,[],0,0,0,4104")

    assert state.target_sensor_id == 4104
    assert state.status_register is None
    assert state.is_blocked is None
    assert state.has_sensor_fault is None
    assert state.is_summer_mode is None


def test_invalid_internal_status_register_is_rejected() -> None:
    with pytest.raises(ZontHeatingConfigParseError):
        parse_heating_circuit_internal_state("#Y20496$3160,3140,[],0,0,0,4104,-1")


def test_parse_heating_mode_configuration() -> None:
    mode = parse_heating_mode_configuration(
        "#Z20504:20,'Выключен',[20496,8362,9171],[0,0,0],"
        "[0,0,0],29,[0,0,0],10,0,10,4,0",
        20504,
    )

    assert mode.object_id == 20504
    assert mode.name == "Выключен"
    assert dict(mode.circuit_targets) == {20496: 0, 8362: 0, 9171: 0}
    assert mode.disables_circuit(8362)
    assert not mode.disables_circuit(9825)


@pytest.mark.parametrize(
    "response",
    [
        "#Z20504:16,'Не режим',[20496],[0]",
        "#Z20504:20,'',[20496],[0]",
        "#Z20504:20,'Выключен',[20496,8362],[0]",
        "#Z20504:20,'Выключен',[20496,20496],[0,0]",
        "#Z20504:20,'Выключен',[20496],[-1]",
    ],
)
def test_invalid_heating_mode_configuration_is_rejected(response: str) -> None:
    with pytest.raises(ZontHeatingConfigParseError):
        parse_heating_mode_configuration(response)


def test_invalid_internal_mode_list_is_rejected() -> None:
    with pytest.raises(ZontHeatingConfigParseError):
        parse_heating_circuit_internal_state(
            "#Y20496$3160,3140,[],0,0,0,4104,1,[20501,-1]"
        )


@pytest.mark.parametrize(
    ("response", "object_type", "lower", "upper"),
    [
        ("#Z4107:1,123,'Спальня',3130,2830,5,300000,[],[],[],0", 1, 10, 40),
        (
            "#Z12001:8,123,18,'Гостиная',3130,2830,5,0,0,0,0,[],[],[],[],0,0,0,[],[],0",
            8,
            10,
            40,
        ),
        (
            "#Z20487:27,1,'Подача',0,3130,2830,5,300000,[],[],[],0,0",
            27,
            10,
            40,
        ),
    ],
)
def test_parse_supported_temperature_sensor_thresholds(
    response: str,
    object_type: int,
    lower: float,
    upper: float,
) -> None:
    configuration = parse_temperature_sensor_configuration(response)

    assert configuration.object_type == object_type
    assert configuration.lower_threshold == lower
    assert configuration.upper_threshold == upper


def test_zero_and_ffff_temperature_thresholds_are_absent() -> None:
    configuration = parse_temperature_sensor_configuration(
        "#Z4107:1,123,'Спальня',65535,0,5,300000,[],[],[],0"
    )

    assert configuration.lower_threshold is None
    assert configuration.upper_threshold is None


@pytest.mark.parametrize(
    "response",
    [
        "#Z20496:!",
        "#Z20496:16,'Радиаторы',3",
        "#Zother:16,'Радиаторы',3,3140,3530,0,0,4104,0,20,[],2,0,0,0,0,0",
        "#Z20496:16,'Радиаторы,3,3140,3530,0,0,4104,0,20,[],2,0,0,0,0,0",
        "#Z20496:16,'Радиаторы',3,3140,3530,0,0,4104,0,20,[1,2,2,0,0,0,0,0",
    ],
)
def test_malformed_configuration_is_rejected(response: str) -> None:
    with pytest.raises(ZontHeatingConfigParseError):
        parse_heating_circuit_configuration(response)


def test_response_id_must_match_request() -> None:
    with pytest.raises(ZontHeatingConfigParseError):
        parse_heating_circuit_internal_state(
            "#Y9171$3090,2980,[],0,0,20501,4103",
            9825,
        )


def test_air_and_pid_ranges_use_sensor_thresholds_or_defaults() -> None:
    sensor = ZontTemperatureSensorConfiguration(4110, 1, 10, 32)

    air = resolve_heating_circuit_control(
        _configuration(setting_register=0),
        4110,
        sensor,
    )
    air_pid = resolve_heating_circuit_control(
        _configuration(setting_register=1),
        4110,
    )

    assert air.control_mode is ZontConsumerControlMode.AIR
    assert not air.has_weather_compensation
    assert (air.min_temperature, air.max_temperature) == (10, 32)
    assert air_pid.control_mode is ZontConsumerControlMode.AIR_PID
    assert not air_pid.has_weather_compensation
    assert (air_pid.min_temperature, air_pid.max_temperature) == (
        AIR_MIN_TEMPERATURE,
        AIR_MAX_TEMPERATURE,
    )


@pytest.mark.parametrize(
    ("setting_register", "expected_mode"),
    [
        (0, ZontConsumerControlMode.AIR),
        (1, ZontConsumerControlMode.AIR_PID),
    ],
)
def test_air_modes_keep_sensor_range_with_weather_compensation(
    setting_register: int,
    expected_mode: ZontConsumerControlMode,
) -> None:
    sensor = ZontTemperatureSensorConfiguration(4110, 1, 10, 32)

    control = resolve_heating_circuit_control(
        _configuration(setting_register=setting_register, pza=9641),
        4110,
        sensor,
    )

    assert control.control_mode is expected_mode
    assert control.has_weather_compensation
    assert (control.min_temperature, control.max_temperature) == (10, 32)


def test_water_range_uses_circuit_sensor_and_final_fallbacks() -> None:
    sensor = ZontTemperatureSensorConfiguration(4104, 1, 0, 90)
    sensor_range = resolve_heating_circuit_control(
        _configuration(
            water_min_temperature=None,
            water_max_temperature=None,
        ),
        4104,
        sensor,
    )
    fallback_range = resolve_heating_circuit_control(
        _configuration(
            water_min_temperature=None,
            water_max_temperature=None,
        ),
        4104,
    )

    assert sensor_range.control_mode is ZontConsumerControlMode.WATER
    assert (sensor_range.min_temperature, sensor_range.max_temperature) == (0, 90)
    assert (fallback_range.min_temperature, fallback_range.max_temperature) == (
        WATER_MIN_TEMPERATURE,
        WATER_MAX_TEMPERATURE,
    )


def test_weather_compensation_request_only_keeps_water_range() -> None:
    control = resolve_heating_circuit_control(
        _configuration(
            setting_register=2 | WEATHER_COMPENSATION_REQUEST_ONLY_FLAG,
            pza=9641,
        ),
        4104,
    )

    assert control.control_mode is ZontConsumerControlMode.WATER
    assert control.has_weather_compensation
    assert (control.min_temperature, control.max_temperature) == (41, 80)


@pytest.mark.parametrize(
    "configuration",
    [
        _configuration(setting_register=2 | SLAVE_MODE_FLAG),
        _configuration(external_thermostat_id=20550),
        _configuration(water_min_temperature=80, water_max_temperature=41),
    ],
)
def test_unsupported_or_invalid_configuration_is_read_only(
    configuration: ZontHeatingCircuitConfiguration,
) -> None:
    control = resolve_heating_circuit_control(configuration, 4104)

    assert not control.can_set_temperature
    assert control.min_temperature is None
    assert control.max_temperature is None


def test_external_thermostat_with_weather_compensation_keeps_setpoint() -> None:
    control = resolve_heating_circuit_control(
        _configuration(external_thermostat_id=20550, pza=9641),
        4104,
    )

    assert control.control_mode is ZontConsumerControlMode.WATER
    assert control.has_weather_compensation
    assert control.can_set_temperature


def test_resolved_control_mapping_is_immutable() -> None:
    controls = immutable_heating_controls(
        {20496: resolve_heating_circuit_control(_configuration(), 4104)}
    )

    with pytest.raises(TypeError):
        controls[9171] = controls[20496]  # type: ignore[index]


def test_internal_state_mapping_is_immutable() -> None:
    states = immutable_heating_states(
        {20496: ZontHeatingCircuitInternalState(20496, 4104, 138)}
    )

    with pytest.raises(TypeError):
        states[9171] = states[20496]  # type: ignore[index]


def test_heating_mode_mapping_is_immutable() -> None:
    modes = immutable_heating_modes(
        {20504: ZontHeatingModeConfiguration(20504, "Выключен", {})}
    )

    with pytest.raises(TypeError):
        modes[20501] = modes[20504]  # type: ignore[index]
