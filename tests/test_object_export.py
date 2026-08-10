"""Tests for Home Assistant temperature exports to ZONT."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import (
    CONF_TEMPERATURE_EXPORTS,
    DOMAIN,
)
from custom_components.zont_ws.object_export import (
    ZontExportSourceError,
    ZontExportSourceUnavailable,
    ZontTemperatureExportBinding,
    ZontTemperatureExportManager,
    export_temperature_command,
    export_temperature_from_state,
    temperature_export_bindings,
    temperature_export_target_ids,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _binding_options() -> dict:
    return {
        CONF_TEMPERATURE_EXPORTS: [
            {
                "source": "sensor.office_temperature",
                "target_id": 4110,
                "target_name": "Т Кабинет",
            }
        ]
    }


def _set_temperature(
    hass: HomeAssistant,
    value: str,
    unit: UnitOfTemperature = UnitOfTemperature.CELSIUS,
) -> None:
    hass.states.async_set(
        "sensor.office_temperature",
        value,
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            ATTR_UNIT_OF_MEASUREMENT: unit,
        },
    )


def test_export_binding_configuration_is_strict_and_deduplicated() -> None:
    options = {
        CONF_TEMPERATURE_EXPORTS: [
            {
                "source": "sensor.one",
                "target_id": 1,
                "target_name": "One",
            },
            {
                "source": "sensor.one",
                "target_id": 2,
                "target_name": "Duplicate source",
            },
            {
                "source": "sensor.three",
                "target_id": 1,
                "target_name": "Duplicate target",
            },
            {"source": "sensor.invalid", "target_id": True, "target_name": "Bad"},
        ]
    }

    assert temperature_export_bindings(options) == (
        ZontTemperatureExportBinding("sensor.one", 1, "One"),
    )
    assert temperature_export_target_ids(options) == frozenset({1})


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("24.14", UnitOfTemperature.CELSIUS, 24.1),
        ("75.2", UnitOfTemperature.FAHRENHEIT, 24.0),
        ("297.15", UnitOfTemperature.KELVIN, 24.0),
    ],
)
def test_temperature_is_converted_to_celsius(
    value: str,
    unit: UnitOfTemperature,
    expected: float,
) -> None:
    state = State(
        "sensor.temperature",
        value,
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            ATTR_UNIT_OF_MEASUREMENT: unit,
        },
    )

    assert export_temperature_from_state(state) == expected
    assert export_temperature_command(expected) == f"1 {expected:.1f}"


def test_invalid_and_unavailable_sources_are_distinguished() -> None:
    with pytest.raises(ZontExportSourceError):
        export_temperature_from_state(
            State(
                "sensor.not_temperature",
                "24",
                {ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.CELSIUS},
            )
        )

    with pytest.raises(ZontExportSourceUnavailable):
        export_temperature_from_state(
            State(
                "sensor.temperature",
                STATE_UNAVAILABLE,
                {
                    ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                    ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.CELSIUS,
                },
            )
        )


async def test_manager_validates_and_sends_temperature(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binding_options())
    entry.add_to_hass(hass)
    _set_temperature(hass, "24.14")
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет", "t": 23}
    )
    client.async_send_command = AsyncMock(return_value={"Id": 4110, "cmdres": 0})
    manager = ZontTemperatureExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    client.async_get_object_state.assert_awaited_once_with(4110)
    client.async_send_command.assert_awaited_once_with(4110, "1 24.1")
    assert manager.configured_count == 1
    assert manager.active_count == 1
    assert manager.error_count == 0


async def test_manager_skips_unavailable_source_without_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binding_options())
    entry.add_to_hass(hass)
    _set_temperature(hass, STATE_UNAVAILABLE)
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет"}
    )
    client.async_send_command = AsyncMock()
    manager = ZontTemperatureExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    client.async_send_command.assert_not_awaited()
    assert manager.active_count == 0
    assert manager.error_count == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, "temperature_export_4110") is None


async def test_manager_reports_missing_target_and_recovers(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binding_options())
    entry.add_to_hass(hass)
    _set_temperature(hass, "24.1")
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(return_value={"id": 4110, "failed": 1})
    client.async_send_command = AsyncMock(return_value={"id": 4110, "cmdres": 0})
    manager = ZontTemperatureExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    assert manager.error_count == 1
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "temperature_export_4110")
        is not None
    )

    client.async_get_object_state.return_value = {
        "id": 4110,
        "type": 1,
        "name": "Т Кабинет",
    }
    await manager._async_sync_all(validate_targets=True)

    assert manager.active_count == 1
    assert manager.error_count == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, "temperature_export_4110") is None


async def test_manager_tracks_state_changes_and_shutdown(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binding_options())
    entry.add_to_hass(hass)
    _set_temperature(hass, "23.0")
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет"}
    )
    client.async_send_command = AsyncMock(return_value={"id": 4110, "cmdres": 0})
    manager = ZontTemperatureExportManager(hass, entry, client)

    manager.async_start()
    await hass.async_block_till_done()
    assert client.async_send_command.await_count == 1

    _set_temperature(hass, "23.5")
    await hass.async_block_till_done()
    assert client.async_send_command.await_count == 2
    assert client.async_send_command.await_args.args == (4110, "1 23.5")

    await manager.async_shutdown()
    _set_temperature(hass, "24.0")
    await hass.async_block_till_done()
    assert client.async_send_command.await_count == 2


async def test_manager_applies_bindings_without_replacing_client(
    hass: HomeAssistant,
) -> None:
    """An export added in options starts on the existing shared client."""
    entry = MockConfigEntry(domain=DOMAIN, options={})
    entry.add_to_hass(hass)
    _set_temperature(hass, "22.5")
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет"}
    )
    client.async_send_command = AsyncMock(return_value={"id": 4110, "cmdres": 0})
    manager = ZontTemperatureExportManager(hass, entry, client)
    manager.async_start()

    await manager.async_reconfigure(_binding_options())
    await hass.async_block_till_done()

    client.async_send_command.assert_awaited_once_with(4110, "1 22.5")
    assert manager.configured_count == 1

    await manager.async_reconfigure({})
    _set_temperature(hass, "23.0")
    await hass.async_block_till_done()

    assert client.async_send_command.await_count == 1
    assert manager.configured_count == 0
    await manager.async_shutdown()
