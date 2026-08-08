"""Tests for the ZONT connectivity sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.zont_ws.binary_sensor import (
    ZontAnalogInputTriggeredBinarySensor,
    ZontCloudConnectedBinarySensor,
    ZontConnectedBinarySensor,
    ZontRadioTriggeredBinarySensor,
    async_setup_entry,
)
from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import DOMAIN, connection_signal
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
from custom_components.zont_ws.objects import (
    ZontAnalogInputData,
    ZontObject,
    ZontRadioSensorData,
    immutable_objects,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _object_entry(obj: ZontObject) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({obj.object_id: obj}),
    )
    coordinator.async_add_listener.return_value = lambda: None
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


async def test_connection_state_updates(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    entity = ZontConnectedBinarySensor(entry)
    entity.hass = hass
    entity.entity_id = "binary_sensor.zont_connected"
    entity.async_write_ha_state = MagicMock()

    assert entity.is_on
    assert entity.unique_id == "ABCDEF123456_connected"
    assert entity.suggested_object_id == "connected"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456")}
    assert "name" not in entity.device_info
    assert "model" not in entity.device_info
    await entity.async_added_to_hass()
    async_dispatcher_send(hass, connection_signal(entry.entry_id), False)

    assert not entity.is_on
    assert entity.available
    entity.async_write_ha_state.assert_called_once()
    await entity.async_remove()


async def test_cloud_connection_uses_shared_snapshot(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(
            info=None,
            server_status=ZontServerStatus(
                cloud_connected=True,
                channels=frozenset({ZontCommunicationChannel.WIFI}),
            ),
        )
    )
    entry.runtime_data = ZontRuntimeData(client, coordinator)

    entity = ZontCloudConnectedBinarySensor(entry)

    assert entity.available
    assert entity.is_on
    assert entity.unique_id == "ABCDEF123456_cloud_connected"
    assert entity.suggested_object_id == "cloud_connected"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456")}


@pytest.mark.parametrize(
    ("subtype", "device_class"),
    [
        (0, BinarySensorDeviceClass.PROBLEM),
        (1, BinarySensorDeviceClass.PROBLEM),
        (2, BinarySensorDeviceClass.PROBLEM),
        (3, BinarySensorDeviceClass.DOOR),
        (4, BinarySensorDeviceClass.MOTION),
        (5, BinarySensorDeviceClass.SMOKE),
        (6, BinarySensorDeviceClass.MOISTURE),
        (7, BinarySensorDeviceClass.MOTION),
        (8, BinarySensorDeviceClass.PROBLEM),
        (9, BinarySensorDeviceClass.PROBLEM),
        (10, BinarySensorDeviceClass.PROBLEM),
        (11, BinarySensorDeviceClass.POWER),
        (12, BinarySensorDeviceClass.PROBLEM),
        (13, BinarySensorDeviceClass.PROBLEM),
        (14, None),
        (15, BinarySensorDeviceClass.SAFETY),
        (16, BinarySensorDeviceClass.PROBLEM),
        (17, BinarySensorDeviceClass.PROBLEM),
        (18, BinarySensorDeviceClass.PROBLEM),
        (19, None),
        (20, None),
        (21, BinarySensorDeviceClass.PROBLEM),
        (22, BinarySensorDeviceClass.PROBLEM),
    ],
)
def test_analog_trigger_maps_subtype_device_class(
    subtype: int,
    device_class: BinarySensorDeviceClass | None,
) -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход",
        subtype=subtype,
        value=1,
        unit_code=8,
        triggered=True,
    )
    entry = _object_entry(analog_input)

    entity = ZontAnalogInputTriggeredBinarySensor(entry, 20550, subtype)

    assert entity.available
    assert entity.is_on
    assert entity.device_class is device_class
    assert entity.unique_id == "ABCDEF123456_20550_triggered"
    assert entity.suggested_object_id == "triggered"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:20550")}


def test_analog_trigger_tracks_field_and_object_availability() -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход",
        subtype=3,
        value=0,
        triggered=None,
    )
    entry = _object_entry(analog_input)
    entity = ZontAnalogInputTriggeredBinarySensor(entry, 20550, 3)

    assert not entity.available

    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                20550: ZontAnalogInputData(
                    object_id=20550,
                    object_type=0,
                    name="Вход",
                    available=False,
                    subtype=3,
                    value=0,
                    triggered=True,
                )
            }
        ),
    )

    assert not entity.available
    assert entity.is_on


async def test_setup_adds_analog_trigger_without_duplicates(
    hass: HomeAssistant,
) -> None:
    analog_input = ZontAnalogInputData(
        object_id=20550,
        object_type=0,
        name="Вход",
        available=False,
        subtype=3,
    )
    entry = _object_entry(analog_input)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert len(async_add_entities.call_args_list[0].args[0]) == 2
    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontAnalogInputTriggeredBinarySensor)
    assert not entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2


@pytest.mark.parametrize(
    ("subtype", "device_class"),
    [
        (10, BinarySensorDeviceClass.MOISTURE),
        (11, BinarySensorDeviceClass.MOTION),
    ],
)
def test_radio_trigger_maps_supported_subtype_device_class(
    subtype: int,
    device_class: BinarySensorDeviceClass,
) -> None:
    radio_sensor = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Радиодатчик",
        subtype=subtype,
        triggered=True,
    )
    entry = _object_entry(radio_sensor)

    entity = ZontRadioTriggeredBinarySensor(entry, 12001, subtype)

    assert entity.available
    assert entity.is_on
    assert entity.device_class is device_class
    assert entity.unique_id == "ABCDEF123456_12001_triggered"
    assert entity.suggested_object_id == "triggered"
    assert entity.device_info["identifiers"] == {(DOMAIN, "ABCDEF123456:object:12001")}


async def test_setup_adds_only_supported_radio_triggers(hass: HomeAssistant) -> None:
    radio_sensor = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Протечка",
        available=False,
        subtype=10,
    )
    entry = _object_entry(radio_sensor)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args_list[1].args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontRadioTriggeredBinarySensor)
    assert not entities[0].available

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 2


async def test_setup_skips_radio_trigger_for_other_subtypes(
    hass: HomeAssistant,
) -> None:
    radio_sensor = ZontRadioSensorData(
        object_id=12001,
        object_type=8,
        name="Радиотермометр",
        subtype=5,
        triggered=True,
    )
    entry = _object_entry(radio_sensor)
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert async_add_entities.call_count == 1
