"""Tests for the ZONT Local options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from custom_components.zont_local.const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONF_DHW_ON_TEMPERATURE,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_EXPORT_KIND,
    CONF_EXPORT_SOURCE,
    CONF_EXPORT_TARGET_ID,
    CONF_EXPORT_TARGET_NAME,
    CONF_EXPORT_TARGET_SUBTYPE,
    CONF_EXPORTS,
    CONF_HEATING_OFF_MODE_ID,
    CONF_IMPORTED_OBJECT_IDS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.export import ZontExportKind
from custom_components.zont_local.flows import discovery as zont_flow_discovery
from custom_components.zont_local.flows import export as zont_export_flow
from custom_components.zont_local.flows import options as zont_options_flow
from custom_components.zont_local.flows.schemas import (
    _validate_dhw_on_temperature,
    _validate_scan_interval,
)
from custom_components.zont_local.protocol import ZontClient
from custom_components.zont_local.protocol.controller import ZontControllerInfo
from custom_components.zont_local.protocol.heating_config import (
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
)
from custom_components.zont_local.protocol.heating_modes import ZontHeatingModeDiscovery
from custom_components.zont_local.protocol.objects import (
    ZontAnalogInputData,
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontObject,
)
from custom_components.zont_local.runtime import ZontRuntimeData
from homeassistant import data_entry_flow
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    STATE_OFF,
    STATE_ON,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

SERIAL_NUMBER = "ABCDEF123456"
CONTROLLER_INFO = ZontControllerInfo(
    serial_number=SERIAL_NUMBER,
    model="H1V02 PRO",
    board_model="700",
    firmware_version="625",
)
AUTO_TITLE = "ZONT H1V02 PRO (192.0.2.10)"
ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
    CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
    CONF_AUTO_TITLE: AUTO_TITLE,
}
OFF_MODE_ID = 20504
OFF_MODE_DISCOVERY = ZontHeatingModeDiscovery(
    circuits={
        8362: ZontHeatingCircuitData(8362, 16, "ГВС", subtype=1),
        20496: ZontHeatingCircuitData(20496, 16, "Радиаторы", subtype=3),
    },
    states={
        8362: ZontHeatingCircuitInternalState(8362, 4097, 0, (OFF_MODE_ID,)),
        20496: ZontHeatingCircuitInternalState(20496, 4104, 0, (OFF_MODE_ID,)),
    },
    modes={
        OFF_MODE_ID: ZontHeatingModeConfiguration(
            OFF_MODE_ID,
            "Выключен",
            {8362: 0, 20496: 0},
        )
    },
)


def _mock_mode_discovery(monkeypatch, discovery=OFF_MODE_DISCOVERY) -> AsyncMock:
    mock = AsyncMock(return_value=discovery)
    monkeypatch.setattr(
        zont_flow_discovery,
        "async_discover_heating_modes",
        mock,
    )
    return mock


async def test_options_flow_updates_off_mode(hass: HomeAssistant, monkeypatch) -> None:
    second_mode = ZontHeatingModeConfiguration(
        20505,
        "Отъезд",
        {8362: 0, 20496: 0},
    )
    discovery = ZontHeatingModeDiscovery(
        circuits=OFF_MODE_DISCOVERY.circuits,
        states={
            circuit_id: ZontHeatingCircuitInternalState(
                state.object_id,
                state.target_sensor_id,
                state.status_register,
                (OFF_MODE_ID, second_mode.object_id),
            )
            for circuit_id, state in OFF_MODE_DISCOVERY.states.items()
        },
        modes={**OFF_MODE_DISCOVERY.modes, second_mode.object_id: second_mode},
    )
    discover = _mock_mode_discovery(monkeypatch, discovery)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
        options={CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is data_entry_flow.FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )
    assert result["step_id"] == "general"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_HEATING_OFF_MODE_ID: str(second_mode.object_id),
            CONF_DHW_ON_TEMPERATURE: 55,
            CONF_SCAN_INTERVAL: MAX_SCAN_INTERVAL,
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_HEATING_OFF_MODE_ID: second_mode.object_id,
        CONF_DHW_ON_TEMPERATURE: 55.0,
        CONF_SCAN_INTERVAL: MAX_SCAN_INTERVAL,
    }
    discover.assert_awaited_once()


async def test_options_flow_updates_imported_devices(
    hass: HomeAssistant, monkeypatch
) -> None:
    discover_objects = AsyncMock(return_value=(dict(OFF_MODE_DISCOVERY.circuits), None))
    monkeypatch.setattr(zont_options_flow, "_async_get_entry_objects", discover_objects)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
        options={
            CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID,
            CONF_DHW_ON_TEMPERATURE: 55.0,
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )

    assert result["step_id"] == "devices"
    selectors = {
        marker.schema: selector
        for marker, selector in result["data_schema"].schema.items()
    }
    labels = [
        option["label"]
        for option in selectors[CONF_IMPORTED_OBJECT_IDS].config["options"]
    ]
    assert labels == [
        "Контур ГВС - ГВС (ID 8362)",
        "Контур потребителя - Радиаторы (ID 20496)",
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_IMPORTED_OBJECT_IDS: ["8362"],
            CONF_AUTO_IMPORT_NEW_OBJECTS: False,
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID,
        CONF_DHW_ON_TEMPERATURE: 55.0,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        CONF_IMPORTED_OBJECT_IDS: [8362],
        CONF_EXCLUDED_OBJECT_IDS: [20496],
        CONF_AUTO_IMPORT_NEW_OBJECTS: False,
    }
    discover_objects.assert_awaited_once_with(hass, entry)


async def test_loaded_device_options_reuse_coordinator_connection(
    hass: HomeAssistant, monkeypatch
) -> None:
    temporary_discovery = AsyncMock()
    monkeypatch.setattr(
        zont_flow_discovery,
        "_async_discover_objects",
        temporary_discovery,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
        options={CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID},
    )
    entry.add_to_hass(hass)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    coordinator = MagicMock()
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=OFF_MODE_DISCOVERY.circuits,
    )
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )

    assert result["step_id"] == "devices"
    coordinator.async_request_refresh.assert_awaited_once()
    temporary_discovery.assert_not_awaited()


@pytest.mark.parametrize("temperature", [4.9, 75.1, float("nan"), True, "invalid"])
def test_dhw_on_temperature_validation_rejects_invalid_values(
    temperature: object,
) -> None:
    assert _validate_dhw_on_temperature({CONF_DHW_ON_TEMPERATURE: temperature}) is None


@pytest.mark.parametrize(
    "interval",
    [
        MIN_SCAN_INTERVAL - 1,
        MAX_SCAN_INTERVAL + 1,
        10.5,
        float("nan"),
        True,
        "60",
    ],
)
def test_scan_interval_validation_rejects_invalid_values(interval: object) -> None:
    assert _validate_scan_interval({CONF_SCAN_INTERVAL: interval}) is None


@pytest.mark.parametrize("interval", [MIN_SCAN_INTERVAL, 60.0, MAX_SCAN_INTERVAL])
def test_scan_interval_validation_accepts_integer_seconds(
    interval: int | float,
) -> None:
    assert _validate_scan_interval({CONF_SCAN_INTERVAL: interval}) == int(interval)


async def test_loaded_options_flow_reuses_coordinator_data(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The options flow must not open a second controller connection."""
    discover = _mock_mode_discovery(monkeypatch)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
        options={CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID},
    )
    entry.add_to_hass(hass)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    coordinator = MagicMock()
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=OFF_MODE_DISCOVERY.circuits,
        heating_states=OFF_MODE_DISCOVERY.states,
        heating_modes=OFF_MODE_DISCOVERY.modes,
    )
    coordinator.async_request_refresh = AsyncMock()
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )

    assert result["step_id"] == "general"
    assert result["errors"] == {}
    discover.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


def _loaded_export_entry(
    hass: HomeAssistant,
    *,
    objects: dict[int, ZontObject],
    options: dict | None = None,
) -> tuple[MockConfigEntry, MagicMock, MagicMock]:
    """Create a loaded config entry backed by one mocked runtime connection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=AUTO_TITLE,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
        options=options or {CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID},
    )
    entry.add_to_hass(hass)
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_state = AsyncMock()
    client.async_send_command = AsyncMock()
    client.async_send_named_command = AsyncMock()
    coordinator = MagicMock()
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=objects,
    )
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry, client, coordinator


async def _open_export_options(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    step_id: str,
) -> ConfigFlowResult:
    """Open one export branch of the options flow."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exports"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_flow_links_existing_temperature_sensor(
    hass: HomeAssistant,
) -> None:
    target = ZontDigitalTemperatureSensorData(4110, 1, "Т Кабинет", temperature=23)
    options = {
        CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID,
        CONF_IMPORTED_OBJECT_IDS: [4110],
        CONF_EXCLUDED_OBJECT_IDS: [],
        CONF_AUTO_IMPORT_NEW_OBJECTS: True,
    }
    entry, client, coordinator = _loaded_export_entry(
        hass,
        objects={target.object_id: target},
        options=options,
    )
    hass.states.async_set(
        "sensor.office_temperature",
        "24.14",
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.CELSIUS,
        },
    )
    client.async_get_object_state.return_value = {
        "id": target.object_id,
        "type": 1,
        "name": target.name,
    }
    client.async_send_command.return_value = {"id": target.object_id, "cmdres": 0}

    result = await _open_export_options(hass, entry, "export_link")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"entity_id": "sensor.office_temperature"},
    )
    assert result["step_id"] == "export_link_target"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"target_id": "4110"}
    )
    assert result["step_id"] == "export_link_confirm"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EXPORTS] == [
        {
            CONF_EXPORT_KIND: "temperature",
            CONF_EXPORT_SOURCE: "sensor.office_temperature",
            CONF_EXPORT_TARGET_ID: 4110,
            CONF_EXPORT_TARGET_NAME: "Т Кабинет",
        }
    ]
    assert entry.options[CONF_IMPORTED_OBJECT_IDS] == []
    assert entry.options[CONF_EXCLUDED_OBJECT_IDS] == [4110]
    client.async_get_object_state.assert_awaited_once_with(4110)
    client.async_send_command.assert_awaited_once_with(4110, "1 24.1")
    coordinator.async_request_refresh.assert_awaited_once()


async def test_options_flow_creates_named_temperature_sensor(
    hass: HomeAssistant,
) -> None:
    entry, client, coordinator = _loaded_export_entry(hass, objects={})
    hass.states.async_set(
        "sensor.bedroom_temperature",
        "21.05",
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.CELSIUS,
        },
    )
    client.async_send_named_command.return_value = {"id": 4111, "cmdres": 0}
    client.async_get_object_state.return_value = {
        "id": 4111,
        "type": 1,
        "name": "HA - Спальня",
    }

    result = await _open_export_options(hass, entry, "export_create_source")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "sensor.bedroom_temperature"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "HA - Спальня"}
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EXPORTS] == [
        {
            CONF_EXPORT_KIND: "temperature",
            CONF_EXPORT_SOURCE: "sensor.bedroom_temperature",
            CONF_EXPORT_TARGET_ID: 4111,
            CONF_EXPORT_TARGET_NAME: "HA - Спальня",
        }
    ]
    assert entry.options[CONF_EXCLUDED_OBJECT_IDS] == [4111]
    client.async_send_named_command.assert_awaited_once_with(
        "HA - Спальня", 1, "1 21.1"
    )
    client.async_get_object_state.assert_awaited_once_with(4111)
    coordinator.async_request_refresh.assert_awaited_once()


async def test_options_flow_creates_named_binary_sensor(
    hass: HomeAssistant,
) -> None:
    entry, client, coordinator = _loaded_export_entry(hass, objects={})
    hass.states.async_set(
        "binary_sensor.test_motion",
        STATE_OFF,
        {ATTR_DEVICE_CLASS: "motion"},
    )
    client.async_send_named_command.return_value = {"id": 4116, "cmdres": 0}
    client.async_get_object_state.return_value = {
        "id": 4116,
        "type": 0,
        "stype": 19,
        "name": "HA - Тест движения",
        "trig": 1,
    }

    result = await _open_export_options(hass, entry, "export_create_source")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "binary_sensor.test_motion"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "HA - Тест движения", "target_subtype": "19"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EXPORTS] == [
        {
            CONF_EXPORT_KIND: "binary",
            CONF_EXPORT_SOURCE: "binary_sensor.test_motion",
            CONF_EXPORT_TARGET_ID: 4116,
            CONF_EXPORT_TARGET_NAME: "HA - Тест движения",
            CONF_EXPORT_TARGET_SUBTYPE: 19,
        }
    ]
    assert entry.options[CONF_EXCLUDED_OBJECT_IDS] == [4116]
    client.async_send_named_command.assert_awaited_once_with(
        "HA - Тест движения",
        0,
        "0 0 180",
        object_subtype=19,
    )
    client.async_get_object_state.assert_awaited_once_with(4116)
    coordinator.async_request_refresh.assert_awaited_once()


async def test_binary_export_creation_requires_explicit_contact_type(
    hass: HomeAssistant,
) -> None:
    entry, _, _ = _loaded_export_entry(hass, objects={})
    hass.states.async_set("binary_sensor.generic", STATE_OFF)

    result = await _open_export_options(hass, entry, "export_create_source")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "binary_sensor.generic"}
    )

    fields = {marker.schema: marker for marker in result["data_schema"].schema}
    subtype = fields["target_subtype"]
    assert isinstance(subtype, vol.Required)
    assert subtype.default is vol.UNDEFINED


async def test_options_flow_links_existing_binary_sensor(
    hass: HomeAssistant,
) -> None:
    target = ZontAnalogInputData(
        4116,
        0,
        "Дверь кабинета",
        subtype=19,
        value=0,
        triggered=False,
    )
    entry, client, coordinator = _loaded_export_entry(
        hass,
        objects={target.object_id: target},
    )
    hass.states.async_set(
        "binary_sensor.office_connected",
        STATE_ON,
        {ATTR_DEVICE_CLASS: "connectivity"},
    )
    client.async_get_object_state.return_value = {
        "id": target.object_id,
        "type": 0,
        "stype": 19,
        "name": target.name,
    }
    client.async_send_command.return_value = {"id": target.object_id, "cmdres": 0}

    result = await _open_export_options(hass, entry, "export_link")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "binary_sensor.office_connected"}
    )
    assert result["step_id"] == "export_link_target"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"target_id": "4116"}
    )
    assert result["step_id"] == "export_link_confirm"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EXPORTS][0][CONF_EXPORT_KIND] == "binary"
    assert entry.options[CONF_EXPORTS][0][CONF_EXPORT_TARGET_SUBTYPE] == 19
    client.async_get_object_state.assert_awaited_once_with(4116)
    client.async_send_command.assert_awaited_once_with(4116, "0 20 180")
    coordinator.async_request_refresh.assert_awaited_once()


async def test_options_flow_rebinds_binary_export_and_updates_subtype(
    hass: HomeAssistant,
) -> None:
    current = ZontAnalogInputData(4116, 0, "Дверь НЗ", subtype=20)
    replacement = ZontAnalogInputData(4119, 0, "Дверь НР", subtype=19)
    options = {
        CONF_IMPORTED_OBJECT_IDS: [],
        CONF_EXCLUDED_OBJECT_IDS: [4116],
        CONF_AUTO_IMPORT_NEW_OBJECTS: True,
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: "binary",
                CONF_EXPORT_SOURCE: "binary_sensor.office_door",
                CONF_EXPORT_TARGET_ID: 4116,
                CONF_EXPORT_TARGET_NAME: current.name,
                CONF_EXPORT_TARGET_SUBTYPE: 20,
            }
        ],
    }
    entry, client, coordinator = _loaded_export_entry(
        hass,
        objects={current.object_id: current, replacement.object_id: replacement},
        options=options,
    )
    hass.states.async_set("binary_sensor.office_door", STATE_ON)
    client.async_get_object_state.return_value = {
        "id": replacement.object_id,
        "type": 0,
        "stype": 19,
        "name": replacement.name,
    }
    client.async_send_command.return_value = {
        "id": replacement.object_id,
        "cmdres": 0,
    }

    result = await _open_export_options(hass, entry, "export_manage")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"binding_id": "4116"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "export_rebind"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"target_id": "4119"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EXPORTS] == [
        {
            CONF_EXPORT_KIND: "binary",
            CONF_EXPORT_SOURCE: "binary_sensor.office_door",
            CONF_EXPORT_TARGET_ID: 4119,
            CONF_EXPORT_TARGET_NAME: replacement.name,
            CONF_EXPORT_TARGET_SUBTYPE: 19,
        }
    ]
    assert entry.options[CONF_EXCLUDED_OBJECT_IDS] == [4116, 4119]
    client.async_send_command.assert_awaited_once_with(4119, "0 20 180")
    coordinator.async_request_refresh.assert_awaited_once()


async def test_options_flow_changes_binary_source_and_preserves_subtype(
    hass: HomeAssistant,
) -> None:
    target = ZontAnalogInputData(4119, 0, "Вход НР", subtype=19)
    options = {
        CONF_IMPORTED_OBJECT_IDS: [],
        CONF_EXCLUDED_OBJECT_IDS: [4119],
        CONF_AUTO_IMPORT_NEW_OBJECTS: True,
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: "binary",
                CONF_EXPORT_SOURCE: "binary_sensor.old_source",
                CONF_EXPORT_TARGET_ID: 4119,
                CONF_EXPORT_TARGET_NAME: target.name,
                CONF_EXPORT_TARGET_SUBTYPE: 19,
            }
        ],
    }
    entry, client, _ = _loaded_export_entry(
        hass,
        objects={target.object_id: target},
        options=options,
    )
    hass.states.async_set("binary_sensor.old_source", STATE_OFF)
    hass.states.async_set("binary_sensor.new_source", STATE_ON)
    client.async_get_object_state.return_value = {
        "id": target.object_id,
        "type": 0,
        "stype": 19,
        "name": target.name,
    }
    client.async_send_command.return_value = {"id": target.object_id, "cmdres": 0}

    result = await _open_export_options(hass, entry, "export_manage")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"binding_id": "4119"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "export_change_source"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "binary_sensor.new_source"}
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    binding = entry.options[CONF_EXPORTS][0]
    assert binding[CONF_EXPORT_SOURCE] == "binary_sensor.new_source"
    assert binding[CONF_EXPORT_TARGET_SUBTYPE] == 19
    client.async_send_command.assert_awaited_once_with(4119, "0 20 180")


def test_available_binary_targets_include_only_confirmed_subtypes() -> None:
    objects = {
        4118: ZontAnalogInputData(4118, 0, "Другой вход", subtype=3),
        4119: ZontAnalogInputData(4119, 0, "Вход НР", subtype=19),
        4120: ZontAnalogInputData(4120, 0, "Вход НЗ", subtype=20),
    }

    targets = zont_export_flow._available_targets(
        objects,
        (),
        ZontExportKind.BINARY,
    )

    assert set(targets) == {4119, 4120}


def test_removing_export_keeps_old_target_excluded() -> None:
    options = {
        CONF_IMPORTED_OBJECT_IDS: [4110],
        CONF_EXCLUDED_OBJECT_IDS: [],
        CONF_AUTO_IMPORT_NEW_OBJECTS: True,
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: "temperature",
                CONF_EXPORT_SOURCE: "sensor.office_temperature",
                CONF_EXPORT_TARGET_ID: 4110,
                CONF_EXPORT_TARGET_NAME: "Т Кабинет",
            }
        ],
    }

    updated = zont_export_flow._options_with_export_bindings(options, ())

    assert updated[CONF_EXPORTS] == []
    assert updated[CONF_IMPORTED_OBJECT_IDS] == []
    assert updated[CONF_EXCLUDED_OBJECT_IDS] == [4110]
