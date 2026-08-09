"""Tests for ZONT consumer heating climate entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_ws.client import (
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
    ZontWsClient,
)
from custom_components.zont_ws.climate import (
    TARGET_TEMPERATURE_STEP,
    ZontConsumerClimate,
    async_setup_entry,
)
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
    ZontDataUpdateCoordinator,
    ZontRuntimeData,
)
from custom_components.zont_ws.heating_config import (
    ZontConsumerControlMode,
    ZontHeatingCircuitControlData,
    immutable_heating_controls,
)
from custom_components.zont_ws.objects import (
    ZontHeatingCircuitData,
    ZontHeatingCircuitMode,
    immutable_objects,
)
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(
    objects: dict[int, ZontHeatingCircuitData],
    controls: dict[int, ZontHeatingCircuitControlData] | None = None,
) -> tuple[MockConfigEntry, MagicMock, MagicMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    client.async_send_command = AsyncMock(
        side_effect=lambda object_id, command: {"id": object_id, "cmdres": 0}
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(objects),
        heating_controls=immutable_heating_controls(controls),
    )
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.async_refresh_object = AsyncMock(return_value=True)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry, client, coordinator


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
