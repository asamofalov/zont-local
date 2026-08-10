"""Tests for ZONT consumer heating climate entities."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_ws.climate import async_setup_entry
from custom_components.zont_ws.const import CONF_HEATING_OFF_MODE_ID, DOMAIN
from custom_components.zont_ws.coordinator import (
    ZontDataUpdateCoordinator,
)
from custom_components.zont_ws.data import ZontControllerData, ZontData
from custom_components.zont_ws.entities.heating.climate import (
    TARGET_TEMPERATURE_STEP,
    ZontConsumerClimate,
)
from custom_components.zont_ws.protocol import (
    ZontClient,
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from custom_components.zont_ws.protocol.heating_config import (
    ZontConsumerControlMode,
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
    immutable_heating_controls,
    immutable_heating_modes,
    immutable_heating_states,
)
from custom_components.zont_ws.protocol.objects import (
    ZontHeatingCircuitData,
    ZontHeatingCircuitMode,
    immutable_objects,
)
from custom_components.zont_ws.runtime import ZontRuntimeData
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(
    objects: dict[int, ZontHeatingCircuitData],
    controls: dict[int, ZontHeatingCircuitControlData] | None = None,
    *,
    off_mode_id: int | None = None,
    modes: dict[int, ZontHeatingModeConfiguration] | None = None,
    states: dict[int, ZontHeatingCircuitInternalState] | None = None,
) -> tuple[MockConfigEntry, MagicMock, MagicMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
        options=(
            {CONF_HEATING_OFF_MODE_ID: off_mode_id} if off_mode_id is not None else {}
        ),
    )
    client = MagicMock(spec=ZontClient)
    client.async_send_command = AsyncMock(
        side_effect=lambda object_id, command: {"id": object_id, "cmdres": 0}
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(objects),
        heating_controls=immutable_heating_controls(controls),
        heating_states=immutable_heating_states(states),
        heating_modes=immutable_heating_modes(modes),
    )
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.async_refresh_object = AsyncMock(return_value=True)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry, client, coordinator


def _mode(
    mode_id: int,
    name: str,
    targets: dict[int, int],
) -> ZontHeatingModeConfiguration:
    return ZontHeatingModeConfiguration(mode_id, name, targets)


def _state(*mode_ids: int) -> ZontHeatingCircuitInternalState:
    return ZontHeatingCircuitInternalState(20496, 4104, 1, mode_ids)


def _circuit(
    object_id: int = 20496,
    *,
    subtype: int = 3,
    available: bool = True,
    mode: ZontHeatingCircuitMode = ZontHeatingCircuitMode.HEAT,
    target_temperature: float | None = 42,
) -> ZontHeatingCircuitData:
    return ZontHeatingCircuitData(
        object_id=object_id,
        object_type=16,
        name="Радиаторы" if subtype == 3 else "ГВС",
        available=available,
        subtype=subtype,
        current_temperature=38.5,
        target_temperature=target_temperature,
        mode=mode,
        mode_id=0,
        fault=False,
    )


def _control(
    min_temperature: float = 41,
    max_temperature: float = 80,
) -> ZontHeatingCircuitControlData:
    return ZontHeatingCircuitControlData(
        control_mode=ZontConsumerControlMode.WATER,
        has_weather_compensation=False,
        target_sensor_id=4104,
        min_temperature=min_temperature,
        max_temperature=max_temperature,
    )


def test_climate_exposes_target_only_and_observed_mode() -> None:
    entry, _, _ = _entry({20496: _circuit()}, {20496: _control()})
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.available
    assert entity.current_temperature == 38.5
    assert entity.target_temperature == 42
    assert entity.hvac_mode is HVACMode.HEAT
    assert entity.hvac_modes == []
    assert entity.hvac_action is None
    assert entity.supported_features == ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.temperature_unit is UnitOfTemperature.CELSIUS
    assert entity.min_temp == 41
    assert entity.max_temp == 80
    assert entity.target_temperature_step == TARGET_TEMPERATURE_STEP
    assert entity.unique_id == "ABCDEF123456_20496_climate"
    assert entity.suggested_object_id is None
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:20496")}


def test_climate_exposes_standard_on_off_with_valid_binding() -> None:
    objects = {
        20496: _circuit(target_temperature=42),
        8362: _circuit(8362, subtype=1, target_temperature=60),
    }
    entry, _, _ = _entry(
        objects,
        {20496: _control()},
        off_mode_id=20504,
        modes={20504: _mode(20504, "Выключен", {20496: 0, 8362: 0})},
        states={20496: _state(20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.hvac_modes == [HVACMode.HEAT, HVACMode.OFF]
    assert entity.supported_features == (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )


def test_climate_rejects_off_mode_that_does_not_cover_dhw() -> None:
    entry, _, _ = _entry(
        {
            20496: _circuit(target_temperature=42),
            8362: _circuit(8362, subtype=1, target_temperature=60),
        },
        {20496: _control()},
        off_mode_id=20504,
        modes={20504: _mode(20504, "Частичный", {20496: 0})},
        states={20496: _state(20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.hvac_modes == []
    assert entity.preset_modes == ["Частичный"]
    assert entity.supported_features == (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )


def test_climate_exposes_applicable_presets_independently_of_other_controls() -> None:
    """Expose named modes without temperature control or an off-mode binding."""
    entry, _, _ = _entry(
        {20496: replace(_circuit(mode=ZontHeatingCircuitMode.OFF), mode_id=20504)},
        modes={
            20501: _mode(20501, "Комфорт", {20496: 3150}),
            20502: _mode(20502, "Другой контур", {9171: 2980}),
            20504: _mode(20504, "Выключен", {20496: 0}),
        },
        states={20496: _state(20504, 20502, 20501)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.preset_modes == ["Выключен", "Комфорт"]
    assert entity.preset_mode == "Выключен"
    assert entity.hvac_mode is HVACMode.OFF
    assert entity.hvac_modes == []
    assert entity.supported_features is ClimateEntityFeature.PRESET_MODE


def test_climate_disambiguates_duplicate_and_literal_preset_names() -> None:
    """Generate stable unique labels while retaining every applicable mode."""
    entry, _, _ = _entry(
        {20496: replace(_circuit(), mode_id=20501)},
        modes={
            20501: _mode(20501, "Комфорт", {20496: 3150}),
            20502: _mode(20502, "Комфорт", {20496: 3130}),
            20503: _mode(20503, "Комфорт (20501)", {20496: 3110}),
        },
        states={20496: _state(20501, 20502, 20503)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.preset_modes == [
        "Комфорт (20501) [2]",
        "Комфорт (20502)",
        "Комфорт (20501)",
    ]
    assert entity.preset_mode == "Комфорт (20501) [2]"


def test_climate_has_no_current_preset_for_manual_or_unknown_mode() -> None:
    """Do not infer a named mode from a manual target or stale mode ID."""
    entry, _, coordinator = _entry(
        {20496: _circuit()},
        modes={20501: _mode(20501, "Комфорт", {20496: 3150})},
        states={20496: _state(20501)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.preset_mode is None
    coordinator.data = replace(
        coordinator.data,
        objects=immutable_objects({20496: replace(_circuit(), mode_id=29999)}),
    )
    assert entity.preset_mode is None


def test_climate_preset_follows_coordinator_snapshot() -> None:
    """Reflect push-style mode changes without recreating the entity."""
    entry, _, coordinator = _entry(
        {20496: replace(_circuit(), mode_id=20501)},
        modes={
            20501: _mode(20501, "Комфорт", {20496: 3150}),
            20504: _mode(20504, "Выключен", {20496: 0}),
        },
        states={20496: _state(20501, 20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.preset_mode == "Комфорт"
    coordinator.data = replace(
        coordinator.data,
        objects=immutable_objects(
            {
                20496: replace(
                    _circuit(mode=ZontHeatingCircuitMode.OFF),
                    mode_id=20504,
                )
            }
        ),
    )
    assert entity.preset_mode == "Выключен"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ZontHeatingCircuitMode.HEAT, HVACMode.HEAT),
        (ZontHeatingCircuitMode.COOL, HVACMode.COOL),
        (ZontHeatingCircuitMode.OFF, HVACMode.OFF),
    ],
)
def test_climate_maps_observed_mode(
    mode: ZontHeatingCircuitMode,
    expected: HVACMode,
) -> None:
    entry, _, _ = _entry(
        {20496: _circuit(mode=mode)},
        {20496: _control()},
    )

    assert ZontConsumerClimate(entry, 20496).hvac_mode is expected


def test_climate_is_read_only_without_valid_metadata() -> None:
    entry, _, _ = _entry({20496: _circuit()})
    entity = ZontConsumerClimate(entry, 20496)

    assert entity.available
    assert entity.supported_features == ClimateEntityFeature(0)


def test_climate_tracks_object_availability() -> None:
    entry, _, _ = _entry(
        {20496: _circuit(available=False)},
        {20496: _control()},
    )

    assert not ZontConsumerClimate(entry, 20496).available


async def test_setup_adds_only_consumer_circuits(hass: HomeAssistant) -> None:
    entry, _, coordinator = _entry(
        {
            20496: _circuit(),
            8362: _circuit(8362, subtype=1),
        },
        {20496: _control()},
    )
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontConsumerClimate)
    assert entities[0].unique_id == "ABCDEF123456_20496_climate"

    listener = coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 1


@pytest.mark.parametrize(
    ("selected", "initial_mode_id", "expected_mode"),
    [
        ("Комфорт", 0, ZontHeatingCircuitMode.HEAT),
        ("Выключен", 20501, ZontHeatingCircuitMode.OFF),
    ],
)
async def test_set_preset_applies_only_selected_circuit_and_confirms_state(
    selected: str,
    initial_mode_id: int,
    expected_mode: ZontHeatingCircuitMode,
) -> None:
    """Apply active and zero-target presets through confirmed object state."""
    modes = {
        20501: _mode(20501, "Комфорт", {20496: 3150}),
        20504: _mode(20504, "Выключен", {20496: 0}),
    }
    entry, client, coordinator = _entry(
        {20496: replace(_circuit(), mode_id=initial_mode_id)},
        modes=modes,
        states={20496: _state(20501, 20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    async def refresh(object_id: int) -> bool:
        mode_id = int(client.async_send_command.await_args.args[1])
        mode = modes[mode_id]
        circuit = coordinator.data.objects[object_id]
        assert isinstance(circuit, ZontHeatingCircuitData)
        circuit = replace(
            circuit,
            mode_id=mode_id,
            mode=(
                ZontHeatingCircuitMode.OFF
                if mode.circuit_targets[object_id] == 0
                else ZontHeatingCircuitMode.HEAT
            ),
            target_temperature=(None if mode.circuit_targets[object_id] == 0 else 42),
        )
        coordinator.data = replace(
            coordinator.data,
            objects=immutable_objects({object_id: circuit}),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh

    await entity.async_set_preset_mode(selected)

    expected_mode_id = 20504 if selected == "Выключен" else 20501
    client.async_send_command.assert_awaited_once_with(
        20496,
        str(expected_mode_id),
    )
    coordinator.async_refresh_object.assert_awaited_once_with(20496)
    assert entity.preset_mode == selected
    assert (
        entity.hvac_mode
        is {
            ZontHeatingCircuitMode.HEAT: HVACMode.HEAT,
            ZontHeatingCircuitMode.OFF: HVACMode.OFF,
        }[expected_mode]
    )


async def test_set_current_preset_is_noop() -> None:
    """Avoid sending a command when the requested mode is already active."""
    entry, client, coordinator = _entry(
        {20496: replace(_circuit(), mode_id=20501)},
        modes={20501: _mode(20501, "Комфорт", {20496: 3150})},
        states={20496: _state(20501)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    await entity.async_set_preset_mode("Комфорт")

    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_set_unknown_preset_is_rejected() -> None:
    """Reject a stale or unknown Home Assistant preset name."""
    entry, client, coordinator = _entry(
        {20496: _circuit()},
        modes={20501: _mode(20501, "Комфорт", {20496: 3150})},
        states={20496: _state(20501)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_set_preset_mode("Несуществующий")

    assert raised.value.translation_key == "heating_preset_mode_unavailable"
    assert raised.value.translation_placeholders == {"preset_mode": "Несуществующий"}
    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_rejected_preset_command_is_translated() -> None:
    """Translate a controller rejection from the preset command path."""
    entry, client, coordinator = _entry(
        {20496: _circuit()},
        modes={20501: _mode(20501, "Комфорт", {20496: 3150})},
        states={20496: _state(20501)},
    )
    client.async_send_command.side_effect = None
    client.async_send_command.return_value = {"id": 20496, "cmdres": 2}
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_preset_mode("Комфорт")

    assert raised.value.translation_key == "command_rejected"
    coordinator.async_refresh_object.assert_not_awaited()


async def test_set_preset_requires_confirmed_state() -> None:
    """Reject an accepted preset when the controller state does not change."""
    entry, _, coordinator = _entry(
        {20496: _circuit()},
        modes={20501: _mode(20501, "Комфорт", {20496: 3150})},
        states={20496: _state(20501)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_preset_mode("Комфорт")

    assert raised.value.translation_key == "heating_state_not_confirmed"
    coordinator.async_refresh_object.assert_awaited_once_with(20496)


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (ZontConnectionError("offline"), "controller_offline"),
        (ZontCommandTimeoutError("timeout"), "command_timeout"),
        (ZontProtocolError("invalid response"), "protocol_error"),
    ],
)
async def test_preset_protocol_errors_are_translated(
    error: Exception,
    translation_key: str,
) -> None:
    """Translate preset transport and protocol failures for Home Assistant."""
    entry, client, coordinator = _entry(
        {20496: _circuit()},
        modes={20501: _mode(20501, "Комфорт", {20496: 3150})},
        states={20496: _state(20501)},
    )
    client.async_send_command.side_effect = error
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_preset_mode("Комфорт")

    assert raised.value.translation_key == translation_key
    coordinator.async_refresh_object.assert_not_awaited()


@pytest.mark.parametrize(
    "mode",
    [ZontHeatingCircuitMode.HEAT, ZontHeatingCircuitMode.OFF],
)
async def test_set_temperature_works_in_heat_and_off_modes(
    mode: ZontHeatingCircuitMode,
) -> None:
    entry, client, coordinator = _entry(
        {20496: _circuit(mode=mode)},
        {20496: _control()},
    )
    entity = ZontConsumerClimate(entry, 20496)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 42})

    client.async_send_command.assert_awaited_once_with(20496, 3150)
    coordinator.async_refresh_object.assert_awaited_once_with(20496)
    assert entity.target_temperature == 42


async def test_turn_off_and_on_restore_previous_named_mode() -> None:
    objects = {
        20496: replace(_circuit(target_temperature=42), mode_id=20501),
        8362: _circuit(8362, subtype=1, target_temperature=60),
    }
    entry, client, coordinator = _entry(
        objects,
        {20496: _control()},
        off_mode_id=20504,
        modes={
            20501: _mode(20501, "Комфорт", {20496: 3150}),
            20504: _mode(20504, "Выключен", {20496: 0, 8362: 0}),
        },
        states={20496: _state(20501, 20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    async def refresh(object_id: int) -> bool:
        command = client.async_send_command.await_args.args[1]
        circuit = coordinator.data.objects[object_id]
        assert isinstance(circuit, ZontHeatingCircuitData)
        if command == "20504":
            circuit = replace(
                circuit,
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=20504,
                target_temperature=None,
            )
        else:
            circuit = replace(
                circuit,
                mode=ZontHeatingCircuitMode.HEAT,
                mode_id=20501,
                target_temperature=42,
            )
        coordinator.data = replace(
            coordinator.data,
            objects=immutable_objects({**coordinator.data.objects, object_id: circuit}),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh

    await entity.async_turn_off()
    assert entity.hvac_mode is HVACMode.OFF
    await entity.async_turn_on()

    assert entity.hvac_mode is HVACMode.HEAT
    assert [call.args for call in client.async_send_command.await_args_list] == [
        (20496, "20504"),
        (20496, "20501"),
    ]


async def test_turn_on_after_restart_uses_minimum_setpoint() -> None:
    entry, client, coordinator = _entry(
        {
            20496: replace(
                _circuit(
                    mode=ZontHeatingCircuitMode.OFF,
                    target_temperature=None,
                ),
                mode_id=20504,
            ),
            8362: _circuit(8362, subtype=1, target_temperature=None),
        },
        {20496: _control()},
        off_mode_id=20504,
        modes={20504: _mode(20504, "Выключен", {20496: 0, 8362: 0})},
        states={20496: _state(20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    await entity.async_turn_on()

    client.async_send_command.assert_awaited_once_with(20496, 3140)
    coordinator.async_refresh_object.assert_awaited_once_with(20496)


async def test_manual_state_replaces_remembered_named_mode() -> None:
    entry, client, coordinator = _entry(
        {
            20496: replace(_circuit(target_temperature=42), mode_id=20501),
            8362: _circuit(8362, subtype=1, target_temperature=60),
        },
        {20496: _control()},
        off_mode_id=20504,
        modes={
            20501: _mode(20501, "Комфорт", {20496: 3150}),
            20504: _mode(20504, "Выключен", {20496: 0, 8362: 0}),
        },
        states={20496: _state(20501, 20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)
    coordinator.data = replace(
        coordinator.data,
        objects=immutable_objects(
            {
                **coordinator.data.objects,
                20496: replace(_circuit(target_temperature=43), mode_id=0),
            }
        ),
    )
    entity._remember_active_state()
    coordinator.data = replace(
        coordinator.data,
        objects=immutable_objects(
            {
                **coordinator.data.objects,
                20496: replace(
                    _circuit(
                        mode=ZontHeatingCircuitMode.OFF,
                        target_temperature=None,
                    ),
                    mode_id=20504,
                ),
            }
        ),
    )

    await entity.async_turn_on()

    client.async_send_command.assert_awaited_once_with(20496, 3160)


async def test_turn_off_requires_confirmed_state() -> None:
    entry, _, coordinator = _entry(
        {
            20496: _circuit(target_temperature=42),
            8362: _circuit(8362, subtype=1, target_temperature=60),
        },
        {20496: _control()},
        off_mode_id=20504,
        modes={20504: _mode(20504, "Выключен", {20496: 0, 8362: 0})},
        states={20496: _state(20504)},
    )
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_turn_off()

    assert raised.value.translation_key == "heating_state_not_confirmed"
    coordinator.async_refresh_object.assert_awaited_once_with(20496)


@pytest.mark.parametrize("temperature", [40.9, 80.1, float("nan"), None, True])
async def test_set_temperature_validates_dynamic_range(temperature: object) -> None:
    entry, client, coordinator = _entry(
        {20496: _circuit()},
        {20496: _control()},
    )
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: temperature})

    assert raised.value.translation_key == "temperature_out_of_range"
    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_set_temperature_rejects_read_only_control() -> None:
    entry, client, coordinator = _entry({20496: _circuit()})
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 42})

    assert raised.value.translation_key == "temperature_control_unavailable"
    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_rejected_temperature_command_is_translated() -> None:
    entry, client, coordinator = _entry(
        {20496: _circuit()},
        {20496: _control()},
    )
    client.async_send_command.side_effect = None
    client.async_send_command.return_value = {"id": 20496, "cmdres": 2}
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 42})

    assert raised.value.translation_key == "command_rejected"
    coordinator.async_refresh_object.assert_not_awaited()


async def test_connection_error_is_translated() -> None:
    entry, client, coordinator = _entry(
        {20496: _circuit()},
        {20496: _control()},
    )
    client.async_send_command.side_effect = ZontConnectionError
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 42})

    assert raised.value.translation_key == "controller_offline"
    coordinator.async_refresh_object.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (ZontCommandTimeoutError("timeout"), "command_timeout"),
        (ZontProtocolError("invalid response"), "protocol_error"),
    ],
)
async def test_protocol_errors_are_translated(
    error: Exception,
    translation_key: str,
) -> None:
    entry, client, coordinator = _entry(
        {20496: _circuit()},
        {20496: _control()},
    )
    client.async_send_command.side_effect = error
    entity = ZontConsumerClimate(entry, 20496)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 42})

    assert raised.value.translation_key == translation_key
    coordinator.async_refresh_object.assert_not_awaited()
