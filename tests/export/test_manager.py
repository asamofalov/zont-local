"""Tests for Home Assistant entity exports to ZONT."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_local.const import (
    CONF_EXPORT_KIND,
    CONF_EXPORT_TARGET_SUBTYPE,
    CONF_EXPORTS,
    DOMAIN,
)
from custom_components.zont_local.export import (
    BINARY_EXPORT_SUBTYPES,
    ZontExportBinding,
    ZontExportKind,
    ZontExportManager,
    ZontExportSourceError,
    ZontExportSourceUnavailable,
    export_binary_command,
    export_binary_from_state,
    export_bindings,
    export_target_ids,
    export_temperature_command,
    export_temperature_from_state,
)
from custom_components.zont_local.protocol import ZontClient
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


def _binary_binding_options(subtype: int = 20) -> dict:
    return {
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: ZontExportKind.BINARY,
                "source": "binary_sensor.office_door",
                "target_id": 4116,
                "target_name": "Дверь кабинета",
                CONF_EXPORT_TARGET_SUBTYPE: subtype,
            }
        ]
    }


def _set_binary(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(
        "binary_sensor.office_door",
        value,
        {},
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
            {
                CONF_EXPORT_KIND: ZontExportKind.BINARY,
                "source": "binary_sensor.five",
                "target_id": 5,
                "target_name": "Five",
                CONF_EXPORT_TARGET_SUBTYPE: 19,
            },
            {"source": "sensor.invalid", "target_id": True, "target_name": "Bad"},
            {
                CONF_EXPORT_KIND: "opening",
                "source": "binary_sensor.legacy",
                "target_id": 3,
                "target_name": "Legacy",
            },
            {
                CONF_EXPORT_KIND: ZontExportKind.BINARY,
                "source": "binary_sensor.no_subtype",
                "target_id": 4,
                "target_name": "No subtype",
            },
        ]
    }

    assert export_bindings(options) == (
        ZontExportBinding(ZontExportKind.TEMPERATURE, "sensor.one", 1, "One"),
        ZontExportBinding(
            ZontExportKind.BINARY,
            "binary_sensor.five",
            5,
            "Five",
            19,
        ),
    )
    assert export_target_ids(options) == frozenset({1, 5})


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
def test_binary_state_and_commands_are_encoded(state: str, expected: bool) -> None:
    source = State("binary_sensor.motion", state, {ATTR_DEVICE_CLASS: "motion"})

    assert export_binary_from_state(source) is expected
    assert export_binary_command(expected) == f"0 {20 if expected else 0} 180"


def test_binary_source_accepts_no_device_class_and_rejects_unavailable() -> None:
    assert export_binary_from_state(State("binary_sensor.generic", STATE_ON)) is True
    with pytest.raises(ZontExportSourceError):
        export_binary_from_state(State("sensor.not_binary", STATE_ON))
    with pytest.raises(ZontExportSourceUnavailable):
        export_binary_from_state(State("binary_sensor.door", STATE_UNAVAILABLE))

    assert frozenset({19, 20}) == BINARY_EXPORT_SUBTYPES


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


@pytest.mark.parametrize("subtype", [19, 20])
async def test_manager_validates_and_sends_binary_state(
    hass: HomeAssistant,
    subtype: int,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binary_binding_options(subtype))
    entry.add_to_hass(hass)
    _set_binary(hass, STATE_OFF)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={
            "id": 4116,
            "type": 0,
            "stype": subtype,
            "name": "Дверь кабинета",
            "trig": 0,
        }
    )
    client.async_send_command = AsyncMock(return_value={"id": 4116, "cmdres": 0})
    manager = ZontExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)
    client.async_send_command.assert_awaited_once_with(4116, "0 0 180")

    _set_binary(hass, STATE_ON)
    await manager._async_sync_all(validate_targets=False)
    assert client.async_send_command.await_args.args == (4116, "0 20 180")


@pytest.mark.parametrize("actual_subtype", [3, 19])
async def test_manager_rejects_wrong_binary_target_subtype(
    hass: HomeAssistant,
    actual_subtype: int,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binary_binding_options())
    entry.add_to_hass(hass)
    _set_binary(hass, STATE_OFF)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock(
        return_value={
            "id": 4116,
            "type": 0,
            "stype": actual_subtype,
            "name": "Геркон",
        }
    )
    client.async_send_command = AsyncMock()
    manager = ZontExportManager(hass, entry, client)

    await manager._async_sync_all(validate_targets=True)

    client.async_send_command.assert_not_awaited()
    assert manager.error_count == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, "binary_export_4116")


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


async def test_manager_skips_unavailable_binary_without_refreshing_timeout(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=_binary_binding_options())
    entry.add_to_hass(hass)
    _set_binary(hass, STATE_UNAVAILABLE)
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
