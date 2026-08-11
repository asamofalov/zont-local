"""Tests for Home Assistant entity exports to ZONT."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_local.const import (
    CONF_EXPORT_KIND,
    CONF_EXPORTS,
    DOMAIN,
)
from custom_components.zont_local.export import (
    ZontExportBinding,
    ZontExportKind,
    ZontExportManager,
    ZontExportSourceError,
    ZontExportSourceUnavailable,
    export_bindings,
    export_opening_command,
    export_opening_from_state,
    export_target_ids,
    export_temperature_command,
    export_temperature_from_state,
)
from custom_components.zont_local.protocol import ZontClient
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _binding_options() -> dict:
    return {
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: ZontExportKind.TEMPERATURE,
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


def _opening_binding_options() -> dict:
    return {
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: ZontExportKind.OPENING,
                "source": "binary_sensor.office_door",
                "target_id": 4116,
                "target_name": "Дверь кабинета",
            }
        ]
    }


def _set_opening(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(
        "binary_sensor.office_door",
        value,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )


def test_export_binding_configuration_is_strict_and_deduplicated() -> None:
    options = {
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: ZontExportKind.TEMPERATURE,
                "source": "sensor.one",
                "target_id": 1,
                "target_name": "One",
            },
            {
                CONF_EXPORT_KIND: ZontExportKind.TEMPERATURE,
                "source": "sensor.one",
                "target_id": 2,
                "target_name": "Duplicate source",
            },
            {
                CONF_EXPORT_KIND: ZontExportKind.TEMPERATURE,
                "source": "sensor.three",
                "target_id": 1,
                "target_name": "Duplicate target",
            },
            {"source": "sensor.invalid", "target_id": True, "target_name": "Bad"},
        ]
    }

    assert export_bindings(options) == (
        ZontExportBinding(ZontExportKind.TEMPERATURE, "sensor.one", 1, "One"),
    )
    assert export_target_ids(options) == frozenset({1})


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


@pytest.mark.parametrize(("state", "expected"), [(STATE_OFF, False), (STATE_ON, True)])
def test_opening_state_and_commands_are_encoded(state: str, expected: bool) -> None:
    source = State(
        "binary_sensor.door",
        state,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )

    assert export_opening_from_state(source) is expected
    assert export_opening_command(expected) == f"0 {20 if expected else 0} 180"


def test_opening_source_rejects_wrong_class_and_unavailable_state() -> None:
    with pytest.raises(ZontExportSourceError):
        export_opening_from_state(
            State(
                "binary_sensor.motion",
                STATE_ON,
                {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.MOTION},
            )
        )
    with pytest.raises(ZontExportSourceUnavailable):
        export_opening_from_state(
            State(
                "binary_sensor.door",
                STATE_UNAVAILABLE,
                {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
            )
        )


async def test_manager_validates_and_sends_temperature(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binding_options())
    entry.add_to_hass(hass)
    _set_temperature(hass, "24.14")
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет", "t": 23}
    )
    client.async_send_command = AsyncMock(return_value={"Id": 4110, "cmdres": 0})
    manager = ZontExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    client.async_get_object_state.assert_awaited_once_with(4110)
    client.async_send_command.assert_awaited_once_with(4110, "1 24.1")
    assert manager.configured_count == 1
    assert manager.active_count == 1
    assert manager.error_count == 0


async def test_manager_validates_and_sends_opening_state(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_opening_binding_options())
    entry.add_to_hass(hass)
    _set_opening(hass, STATE_OFF)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={
            "id": 4116,
            "type": 0,
            "stype": 20,
            "name": "Дверь кабинета",
            "trig": 0,
        }
    )
    client.async_send_command = AsyncMock(return_value={"id": 4116, "cmdres": 0})
    manager = ZontExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)
    client.async_send_command.assert_awaited_once_with(4116, "0 0 180")

    _set_opening(hass, STATE_ON)
    await manager._async_sync_all(validate_targets=False)
    assert client.async_send_command.await_args.args == (4116, "0 20 180")


async def test_manager_rejects_wrong_opening_target_subtype(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_opening_binding_options())
    entry.add_to_hass(hass)
    _set_opening(hass, STATE_OFF)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4116, "type": 0, "stype": 3, "name": "Геркон"}
    )
    client.async_send_command = AsyncMock()
    manager = ZontExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    client.async_send_command.assert_not_awaited()
    assert manager.error_count == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, "opening_export_4116")


async def test_manager_skips_unavailable_source_without_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binding_options())
    entry.add_to_hass(hass)
    _set_temperature(hass, STATE_UNAVAILABLE)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет"}
    )
    client.async_send_command = AsyncMock()
    manager = ZontExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    client.async_send_command.assert_not_awaited()
    assert manager.active_count == 0
    assert manager.error_count == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, "temperature_export_4110") is None


async def test_manager_skips_unavailable_opening_without_refreshing_timeout(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_opening_binding_options())
    entry.add_to_hass(hass)
    _set_opening(hass, STATE_UNAVAILABLE)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4116, "type": 0, "stype": 20}
    )
    client.async_send_command = AsyncMock()
    manager = ZontExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    client.async_send_command.assert_not_awaited()
    assert manager.active_count == 0
    assert manager.error_count == 0


async def test_manager_reports_missing_target_and_recovers(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binding_options())
    entry.add_to_hass(hass)
    _set_temperature(hass, "24.1")
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(return_value={"id": 4110, "failed": 1})
    client.async_send_command = AsyncMock(return_value={"id": 4110, "cmdres": 0})
    manager = ZontExportManager(hass, entry, client)

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
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет"}
    )
    client.async_send_command = AsyncMock(return_value={"id": 4110, "cmdres": 0})
    manager = ZontExportManager(hass, entry, client)

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
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={"id": 4110, "type": 1, "name": "Т Кабинет"}
    )
    client.async_send_command = AsyncMock(return_value={"id": 4110, "cmdres": 0})
    manager = ZontExportManager(hass, entry, client)
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
