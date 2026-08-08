"""Tests for ZONT controller sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.controller import (
    ZontCommunicationChannel,
    ZontServerStatus,
)
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
    ZontDataUpdateCoordinator,
    ZontRuntimeData,
)
from custom_components.zont_ws.sensor import (
    CONNECTION_CHANNEL_STATES,
    ZontConnectionChannelSensor,
    ZontSupplyVoltageSensor,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfElectricPotential
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(controller: ZontControllerData) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(controller=controller)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


def test_connection_channel_sensor_uses_enum_state() -> None:
    entry = _entry(
        ZontControllerData(
            info=None,
            server_status=ZontServerStatus(
                cloud_connected=True,
                channels=frozenset(
                    {
                        ZontCommunicationChannel.GSM,
                        ZontCommunicationChannel.WIFI,
                    }
                ),
            ),
        )
    )

    entity = ZontConnectionChannelSensor(entry)

    assert entity.available
    assert entity.native_value == "gsm_wifi"
    assert entity.options == list(CONNECTION_CHANNEL_STATES)
    assert entity.device_class is SensorDeviceClass.ENUM
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.unique_id == "ABCDEF123456_connection_channel"
    assert entity.suggested_object_id == "connection_channel"


def test_supply_voltage_sensor_uses_native_measurement() -> None:
    entry = _entry(ZontControllerData(info=None, supply_voltage=12.3))

    entity = ZontSupplyVoltageSensor(entry)

    assert entity.available
    assert entity.native_value == 12.3
    assert entity.device_class is SensorDeviceClass.VOLTAGE
    assert entity.native_unit_of_measurement is UnitOfElectricPotential.VOLT
    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.suggested_display_precision == 1
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.unique_id == "ABCDEF123456_supply_voltage"
    assert entity.suggested_object_id == "supply_voltage"


def test_controller_sensors_are_unavailable_without_source_data() -> None:
    entry = _entry(ZontControllerData(info=None))

    assert not ZontConnectionChannelSensor(entry).available
    assert not ZontSupplyVoltageSensor(entry).available
