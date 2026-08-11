"""Tests for ZONT domestic hot water entities."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_local.const import (
    CONF_DHW_ON_TEMPERATURE,
    CONF_HEATING_OFF_MODE_ID,
    DHW_DEFAULT_ON_TEMPERATURE,
    DOMAIN,
)
from custom_components.zont_local.coordinator import (
    ZontDataUpdateCoordinator,
)
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.entities.heating.water_heater import (
    MANUAL_OPERATION,
    MAX_TARGET_TEMPERATURE,
    MIN_TARGET_TEMPERATURE,
    TARGET_TEMPERATURE_STEP,
    ZontDhwWaterHeater,
)
from custom_components.zont_local.protocol import (
    ZontClient,
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from custom_components.zont_local.protocol.heating_config import (
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
    immutable_heating_modes,
    immutable_heating_states,
)
from custom_components.zont_local.protocol.objects import (
    ZontHeatingCircuitData,
    ZontHeatingCircuitMode,
    immutable_objects,
)
from custom_components.zont_local.runtime import ZontRuntimeData
from custom_components.zont_local.water_heater import async_setup_entry
from homeassistant.components.water_heater import (
    STATE_OFF,
    STATE_ON,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(
    objects: dict[int, ZontHeatingCircuitData],
    *,
    off_mode_id: int | None = None,
    dhw_on_temperature: float | None = None,
    modes: dict[int, ZontHeatingModeConfiguration] | None = None,
    states: dict[int, ZontHeatingCircuitInternalState] | None = None,
) -> tuple[MockConfigEntry, MagicMock, MagicMock]:
    options: dict[str, int | float] = {}
    if off_mode_id is not None:
        options[CONF_HEATING_OFF_MODE_ID] = off_mode_id
    if dhw_on_temperature is not None:
        options[CONF_DHW_ON_TEMPERATURE] = dhw_on_temperature
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
        options=options,
    )
    client = MagicMock(spec=ZontClient)
    client.async_send_command = AsyncMock(return_value={"id": 8362, "cmdres": 0})
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(objects),
        heating_states=immutable_heating_states(states),
        heating_modes=immutable_heating_modes(modes),
    )
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.async_refresh_object = AsyncMock(return_value=True)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry, client, coordinator


def _circuit(
    object_id: int = 8362,
    *,
    subtype: int = 1,
    available: bool = True,
    mode: ZontHeatingCircuitMode = ZontHeatingCircuitMode.HEAT,
    mode_id: int = 20501,
    target_temperature: float | None = 60,
) -> ZontHeatingCircuitData:
    return ZontHeatingCircuitData(
        object_id=object_id,
        object_type=16,
        name="ГВС" if subtype == 1 else "Радиаторы",
        available=available,
        subtype=subtype,
        current_temperature=29,
        target_temperature=target_temperature,
        mode=mode,
        mode_id=mode_id,
        fault=False,
    )


def _off_mode() -> ZontHeatingModeConfiguration:
    return ZontHeatingModeConfiguration(20504, "Выключен", {8362: 0})


def _state(*mode_ids: int) -> ZontHeatingCircuitInternalState:
    return ZontHeatingCircuitInternalState(8362, 4097, 0, mode_ids)


def _set_circuit(
    coordinator: MagicMock,
    circuit: ZontHeatingCircuitData,
) -> None:
    coordinator.data = replace(
        coordinator.data,
        objects=immutable_objects({circuit.object_id: circuit}),
    )


def test_water_heater_exposes_only_target_temperature() -> None:
    entry, _, _ = _entry({8362: _circuit()})
    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.available
    assert entity.current_temperature == 29
    assert entity.target_temperature == 60
    assert entity.current_operation == STATE_ON
    assert entity.state == STATE_ON
    assert entity.operation_list is None
    assert entity.supported_features is WaterHeaterEntityFeature.TARGET_TEMPERATURE
    assert entity.temperature_unit is UnitOfTemperature.CELSIUS
    assert entity.min_temp == MIN_TARGET_TEMPERATURE
    assert entity.max_temp == MAX_TARGET_TEMPERATURE
    assert entity.target_temperature_step == TARGET_TEMPERATURE_STEP
    assert entity.unique_id == "ABCDEF123456_8362_water_heater"
    assert entity.suggested_object_id is None
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:8362")}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ZontHeatingCircuitMode.HEAT, STATE_ON),
        (ZontHeatingCircuitMode.OFF, STATE_OFF),
        (ZontHeatingCircuitMode.COOL, None),
    ],
)
def test_water_heater_maps_observed_operation(
    mode: ZontHeatingCircuitMode,
    expected: str | None,
) -> None:
    entry, _, _ = _entry({8362: _circuit(mode=mode)})

    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.current_operation == expected
    assert entity.state == expected


def test_water_heater_exposes_on_off_for_validated_binding() -> None:
    off_mode = _off_mode()
    entry, _, _ = _entry(
        {8362: _circuit()},
        off_mode_id=off_mode.object_id,
        modes={off_mode.object_id: off_mode},
        states={8362: _state(off_mode.object_id)},
    )

    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.supported_features == (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.OPERATION_MODE
    )
    assert entity.operation_list == ["Выключен", MANUAL_OPERATION]


def test_water_heater_hides_on_off_for_invalid_binding() -> None:
    off_mode = _off_mode()
    entry, _, _ = _entry(
        {8362: _circuit()},
        off_mode_id=off_mode.object_id,
        modes={off_mode.object_id: off_mode},
        states={8362: _state()},
    )

    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.supported_features is WaterHeaterEntityFeature.TARGET_TEMPERATURE


def test_water_heater_exposes_applicable_named_operations() -> None:
    """Expose named modes independently of the standard on/off binding."""
    entry, _, _ = _entry(
        {8362: _circuit(mode_id=20503)},
        modes={
            20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330}),
            20502: ZontHeatingModeConfiguration(20502, "Другой", {9171: 2980}),
            20503: ZontHeatingModeConfiguration(20503, "Лето", {8362: 3330}),
            20504: ZontHeatingModeConfiguration(20504, "Выключен", {8362: 0}),
        },
        states={8362: _state(20503, 20502, 20501, 20504)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.operation_list == [
        "Лето",
        "Комфорт",
        "Выключен",
        MANUAL_OPERATION,
    ]
    assert entity.current_operation == "Лето"
    assert entity.state == "Лето"
    assert entity.supported_features == (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
    )


@pytest.mark.parametrize(
    ("circuit", "expected"),
    [
        (
            _circuit(
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=20504,
                target_temperature=None,
            ),
            "Выключен",
        ),
        (_circuit(mode_id=0), MANUAL_OPERATION),
        (
            _circuit(
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=0,
                target_temperature=None,
            ),
            STATE_OFF,
        ),
        (_circuit(mode_id=29999), STATE_ON),
    ],
)
def test_water_heater_maps_named_manual_and_fallback_operations(
    circuit: ZontHeatingCircuitData,
    expected: str,
) -> None:
    """Prefer confirmed names and safely fall back for stale metadata."""
    entry, _, _ = _entry(
        {8362: circuit},
        modes={
            20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330}),
            20504: ZontHeatingModeConfiguration(20504, "Выключен", {8362: 0}),
        },
        states={8362: _state(20501, 20504)},
    )

    assert ZontDhwWaterHeater(entry, 8362).current_operation == expected


def test_water_heater_disambiguates_manual_operation_name() -> None:
    """Keep the local manual operation distinct from a controller mode."""
    entry, _, _ = _entry(
        {8362: _circuit(mode_id=20501)},
        modes={
            20501: ZontHeatingModeConfiguration(
                20501,
                MANUAL_OPERATION,
                {8362: 3330},
            )
        },
        states={8362: _state(20501)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.operation_list == ["Ручной режим (20501)", MANUAL_OPERATION]
    assert entity.current_operation == "Ручной режим (20501)"


def test_water_heater_tracks_object_availability() -> None:
    entry, _, coordinator = _entry({8362: _circuit()})
    entity = ZontDhwWaterHeater(entry, 8362)

    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({8362: _circuit(available=False)}),
    )

    assert not entity.available
    assert entity.current_temperature == 29
    assert entity.target_temperature == 60


async def test_setup_adds_only_dhw_circuits(hass: HomeAssistant) -> None:
    entry, _, coordinator = _entry(
        {
            8362: _circuit(),
            20496: _circuit(20496, subtype=3),
        }
    )
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontDhwWaterHeater)
    assert entities[0].unique_id == "ABCDEF123456_8362_water_heater"

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
async def test_set_operation_applies_named_mode_and_confirms_state(
    selected: str,
    initial_mode_id: int,
    expected_mode: ZontHeatingCircuitMode,
) -> None:
    """Apply active and zero-target modes only to the selected DHW circuit."""
    modes = {
        20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330}),
        20504: ZontHeatingModeConfiguration(20504, "Выключен", {8362: 0}),
    }
    entry, client, coordinator = _entry(
        {8362: _circuit(mode_id=initial_mode_id)},
        modes=modes,
        states={8362: _state(20501, 20504)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    async def refresh_object(object_id: int) -> bool:
        mode_id = int(client.async_send_command.await_args.args[1])
        mode = modes[mode_id]
        _set_circuit(
            coordinator,
            _circuit(
                mode=(
                    ZontHeatingCircuitMode.OFF
                    if mode.circuit_targets[object_id] == 0
                    else ZontHeatingCircuitMode.HEAT
                ),
                mode_id=mode_id,
                target_temperature=(
                    None if mode.circuit_targets[object_id] == 0 else 60
                ),
            ),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh_object

    await entity.async_set_operation_mode(selected)

    expected_mode_id = 20504 if selected == "Выключен" else 20501
    client.async_send_command.assert_awaited_once_with(8362, str(expected_mode_id))
    coordinator.async_refresh_object.assert_awaited_once_with(8362)
    assert entity.current_operation == selected
    assert coordinator.data.objects[8362].mode is expected_mode


async def test_set_manual_operation_reuses_current_target_and_confirms_mode_id() -> (
    None
):
    """Clear a named mode by writing its current target as a manual setpoint."""
    entry, client, coordinator = _entry(
        {8362: _circuit(mode_id=20501, target_temperature=58)},
        modes={20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3310})},
        states={8362: _state(20501)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    async def refresh_object(object_id: int) -> bool:
        _set_circuit(
            coordinator,
            _circuit(mode_id=0, target_temperature=58),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh_object

    await entity.async_set_operation_mode(MANUAL_OPERATION)

    client.async_send_command.assert_awaited_once_with(8362, 3310)
    coordinator.async_refresh_object.assert_awaited_once_with(8362)
    assert entity.current_operation == MANUAL_OPERATION


async def test_set_manual_operation_from_off_uses_configured_temperature() -> None:
    """Use the restart-safe target when no active temperature was observed."""
    entry, client, coordinator = _entry(
        {
            8362: _circuit(
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=20504,
                target_temperature=None,
            )
        },
        dhw_on_temperature=55,
        modes={20504: _off_mode()},
        states={8362: _state(20504)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    async def refresh_object(object_id: int) -> bool:
        _set_circuit(
            coordinator,
            _circuit(mode_id=0, target_temperature=55),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh_object

    await entity.async_set_operation_mode(MANUAL_OPERATION)

    client.async_send_command.assert_awaited_once_with(8362, 3280)
    assert entity.current_operation == MANUAL_OPERATION


@pytest.mark.parametrize(
    ("operation", "mode_id"),
    [("Комфорт", 20501), (MANUAL_OPERATION, 0)],
)
async def test_set_current_operation_is_noop(operation: str, mode_id: int) -> None:
    """Avoid a command when the requested operation is already active."""
    entry, client, coordinator = _entry(
        {8362: _circuit(mode_id=mode_id)},
        modes={20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330})},
        states={8362: _state(20501)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    await entity.async_set_operation_mode(operation)

    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_set_unknown_operation_is_rejected() -> None:
    """Reject stale or unknown operation names without sending a command."""
    entry, client, coordinator = _entry(
        {8362: _circuit()},
        modes={20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330})},
        states={8362: _state(20501)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_set_operation_mode("Несуществующий")

    assert raised.value.translation_key == "heating_operation_mode_unavailable"
    assert raised.value.translation_placeholders == {"operation_mode": "Несуществующий"}
    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_set_operation_requires_confirmed_named_state() -> None:
    """Reject an accepted mode when m_id does not change."""
    entry, _, coordinator = _entry(
        {8362: _circuit(mode_id=0)},
        modes={20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330})},
        states={8362: _state(20501)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_operation_mode("Комфорт")

    assert raised.value.translation_key == "heating_state_not_confirmed"
    coordinator.async_refresh_object.assert_awaited_once_with(8362)


async def test_set_manual_operation_requires_confirmed_manual_state() -> None:
    """Require a manual temperature command to clear the named m_id."""
    entry, _, coordinator = _entry(
        {8362: _circuit(mode_id=20501)},
        modes={20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330})},
        states={8362: _state(20501)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_operation_mode(MANUAL_OPERATION)

    assert raised.value.translation_key == "heating_state_not_confirmed"
    coordinator.async_refresh_object.assert_awaited_once_with(8362)


async def test_rejected_operation_command_is_translated() -> None:
    """Translate a controller rejection from the operation-mode path."""
    entry, client, coordinator = _entry(
        {8362: _circuit(mode_id=0)},
        modes={20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330})},
        states={8362: _state(20501)},
    )
    client.async_send_command.return_value = {"id": 8362, "cmdres": 2}
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_operation_mode("Комфорт")

    assert raised.value.translation_key == "command_rejected"
    coordinator.async_refresh_object.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (ZontConnectionError("offline"), "controller_offline"),
        (ZontCommandTimeoutError("timeout"), "command_timeout"),
        (ZontProtocolError("invalid response"), "protocol_error"),
    ],
)
async def test_operation_protocol_errors_are_translated(
    error: Exception,
    translation_key: str,
) -> None:
    """Translate operation transport and protocol failures for Home Assistant."""
    entry, client, coordinator = _entry(
        {8362: _circuit(mode_id=0)},
        modes={20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330})},
        states={8362: _state(20501)},
    )
    client.async_send_command.side_effect = error
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_operation_mode("Комфорт")

    assert raised.value.translation_key == translation_key
    coordinator.async_refresh_object.assert_not_awaited()


async def test_operation_follows_coordinator_snapshot() -> None:
    """Reflect push-style operation changes without recreating the entity."""
    entry, _, coordinator = _entry(
        {8362: _circuit(mode_id=20501)},
        modes={
            20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330}),
            20503: ZontHeatingModeConfiguration(20503, "Лето", {8362: 3330}),
        },
        states={8362: _state(20501, 20503)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.current_operation == "Комфорт"
    _set_circuit(coordinator, _circuit(mode_id=20503))
    assert entity.current_operation == "Лето"


async def test_turn_off_and_on_restore_session_target() -> None:
    off_mode = _off_mode()
    entry, client, coordinator = _entry(
        {8362: _circuit(target_temperature=58)},
        off_mode_id=off_mode.object_id,
        dhw_on_temperature=55,
        modes={off_mode.object_id: off_mode},
        states={8362: _state(off_mode.object_id)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    async def refresh_object(object_id: int) -> bool:
        command = client.async_send_command.await_args.args[1]
        if isinstance(command, str):
            circuit = _circuit(
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=int(command),
                target_temperature=None,
            )
        else:
            circuit = _circuit(
                mode=ZontHeatingCircuitMode.HEAT,
                mode_id=0,
                target_temperature=command / 10 - 273,
            )
        _set_circuit(coordinator, circuit)
        return True

    coordinator.async_refresh_object.side_effect = refresh_object

    await entity.async_turn_off()
    assert entity.current_operation == "Выключен"
    await entity.async_turn_on()

    assert client.async_send_command.await_args_list == [
        ((8362, "20504"),),
        ((8362, 3310),),
    ]
    assert coordinator.async_refresh_object.await_args_list == [
        ((8362,),),
        ((8362,),),
    ]
    assert entity.current_operation == MANUAL_OPERATION
    assert entity.target_temperature == 58


async def test_turn_on_after_restart_uses_configured_temperature() -> None:
    off_mode = _off_mode()
    entry, client, coordinator = _entry(
        {
            8362: _circuit(
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=off_mode.object_id,
                target_temperature=None,
            )
        },
        off_mode_id=off_mode.object_id,
        dhw_on_temperature=55,
        modes={off_mode.object_id: off_mode},
        states={8362: _state(off_mode.object_id)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    async def refresh_object(object_id: int) -> bool:
        _set_circuit(
            coordinator,
            _circuit(
                mode=ZontHeatingCircuitMode.HEAT,
                mode_id=0,
                target_temperature=55,
            ),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh_object

    await entity.async_turn_on()

    client.async_send_command.assert_awaited_once_with(8362, 3280)
    assert entity.current_operation == MANUAL_OPERATION
    assert entity.target_temperature == 55


async def test_turn_on_old_entry_uses_default_temperature() -> None:
    off_mode = _off_mode()
    entry, client, coordinator = _entry(
        {
            8362: _circuit(
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=off_mode.object_id,
                target_temperature=None,
            )
        },
        off_mode_id=off_mode.object_id,
        modes={off_mode.object_id: off_mode},
        states={8362: _state(off_mode.object_id)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    async def refresh_object(object_id: int) -> bool:
        _set_circuit(
            coordinator,
            _circuit(
                mode=ZontHeatingCircuitMode.HEAT,
                mode_id=0,
                target_temperature=DHW_DEFAULT_ON_TEMPERATURE,
            ),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh_object

    await entity.async_turn_on()

    client.async_send_command.assert_awaited_once_with(8362, 3330)


@pytest.mark.parametrize(
    ("mode", "method"),
    [
        (ZontHeatingCircuitMode.HEAT, "async_turn_on"),
        (ZontHeatingCircuitMode.OFF, "async_turn_off"),
    ],
)
async def test_turn_on_off_are_idempotent(
    mode: ZontHeatingCircuitMode,
    method: str,
) -> None:
    off_mode = _off_mode()
    entry, client, coordinator = _entry(
        {
            8362: _circuit(
                mode=mode,
                mode_id=(
                    off_mode.object_id if mode is ZontHeatingCircuitMode.OFF else 0
                ),
                target_temperature=(None if mode is ZontHeatingCircuitMode.OFF else 60),
            )
        },
        off_mode_id=off_mode.object_id,
        modes={off_mode.object_id: off_mode},
        states={8362: _state(off_mode.object_id)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    await getattr(entity, method)()

    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_turn_on_off_require_validated_binding() -> None:
    entry, client, coordinator = _entry({8362: _circuit()})
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_turn_off()

    assert raised.value.translation_key == "heating_off_mode_unavailable"
    _set_circuit(
        coordinator,
        _circuit(mode=ZontHeatingCircuitMode.OFF, target_temperature=None),
    )
    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_turn_on()

    assert raised.value.translation_key == "heating_turn_on_unavailable"
    client.async_send_command.assert_not_awaited()


@pytest.mark.parametrize(
    ("initial_circuit", "method"),
    [
        (_circuit(), "async_turn_off"),
        (
            _circuit(
                mode=ZontHeatingCircuitMode.OFF,
                mode_id=20504,
                target_temperature=None,
            ),
            "async_turn_on",
        ),
    ],
)
async def test_turn_on_off_require_confirmed_state(
    initial_circuit: ZontHeatingCircuitData,
    method: str,
) -> None:
    off_mode = _off_mode()
    entry, client, coordinator = _entry(
        {8362: initial_circuit},
        off_mode_id=off_mode.object_id,
        modes={off_mode.object_id: off_mode},
        states={8362: _state(off_mode.object_id)},
    )
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(HomeAssistantError) as raised:
        await getattr(entity, method)()

    assert raised.value.translation_key == "heating_state_not_confirmed"
    client.async_send_command.assert_awaited_once()
    coordinator.async_refresh_object.assert_awaited_once_with(8362)


async def test_set_temperature_waits_for_command_and_refreshes_state() -> None:
    entry, client, coordinator = _entry({8362: _circuit()})
    entity = ZontDhwWaterHeater(entry, 8362)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 60.5})

    client.async_send_command.assert_awaited_once_with(8362, 3335)
    coordinator.async_refresh_object.assert_awaited_once_with(8362)
    assert entity.target_temperature == 60


@pytest.mark.parametrize("temperature", [4.9, 75.1, float("nan"), None, True])
async def test_set_temperature_validates_supported_range(
    temperature: object,
) -> None:
    entry, client, coordinator = _entry({8362: _circuit()})
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: temperature})

    assert raised.value.translation_key == "temperature_out_of_range"
    client.async_send_command.assert_not_awaited()
    coordinator.async_refresh_object.assert_not_awaited()


async def test_rejected_temperature_command_is_translated() -> None:
    entry, client, coordinator = _entry({8362: _circuit()})
    client.async_send_command.return_value = {"id": 8362, "cmdres": 2}
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 60})

    assert raised.value.translation_key == "command_rejected"
    coordinator.async_refresh_object.assert_not_awaited()


async def test_connection_error_is_translated() -> None:
    entry, client, coordinator = _entry({8362: _circuit()})
    client.async_send_command.side_effect = ZontConnectionError
    entity = ZontDhwWaterHeater(entry, 8362)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 60})

    assert raised.value.translation_key == "controller_offline"
    coordinator.async_refresh_object.assert_not_awaited()


async def test_failed_readback_does_not_change_successful_command() -> None:
    entry, client, coordinator = _entry({8362: _circuit()})
    coordinator.async_refresh_object.side_effect = ZontConnectionError
    entity = ZontDhwWaterHeater(entry, 8362)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 60})

    client.async_send_command.assert_awaited_once()
    coordinator.async_refresh_object.assert_awaited_once_with(8362)
