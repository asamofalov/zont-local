"""Tests for ZONT domestic hot water entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_ws.client import (
    ZontConnectionError,
    ZontWsClient,
)
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
    ZontDataUpdateCoordinator,
    ZontRuntimeData,
)
from custom_components.zont_ws.objects import (
    ZontHeatingCircuitData,
    ZontHeatingCircuitMode,
    immutable_objects,
)
from custom_components.zont_ws.water_heater import (
    MAX_TARGET_TEMPERATURE,
    MIN_TARGET_TEMPERATURE,
    TARGET_TEMPERATURE_STEP,
    ZontDhwWaterHeater,
    async_setup_entry,
)
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(
    objects: dict[int, ZontHeatingCircuitData],
) -> tuple[MockConfigEntry, MagicMock, MagicMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    client.async_send_command = AsyncMock(return_value={"id": 8362, "cmdres": 0})
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(objects),
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
) -> ZontHeatingCircuitData:
    return ZontHeatingCircuitData(
        object_id=object_id,
        object_type=16,
        name="ГВС" if subtype == 1 else "Радиаторы",
        available=available,
        subtype=subtype,
        current_temperature=29,
        target_temperature=60,
        mode=ZontHeatingCircuitMode.HEAT,
        mode_id=20501,
        fault=False,
    )


def test_water_heater_exposes_only_target_temperature() -> None:
    entry, _, _ = _entry({8362: _circuit()})
    entity = ZontDhwWaterHeater(entry, 8362)

    assert entity.available
    assert entity.current_temperature == 29
    assert entity.target_temperature == 60
    assert entity.current_operation is None
    assert entity.state is None
    assert entity.supported_features is WaterHeaterEntityFeature.TARGET_TEMPERATURE
    assert entity.temperature_unit is UnitOfTemperature.CELSIUS
    assert entity.min_temp == MIN_TARGET_TEMPERATURE
    assert entity.max_temp == MAX_TARGET_TEMPERATURE
    assert entity.target_temperature_step == TARGET_TEMPERATURE_STEP
    assert entity.unique_id == "ABCDEF123456_8362_water_heater"
    assert entity.suggested_object_id is None
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:8362")}


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
