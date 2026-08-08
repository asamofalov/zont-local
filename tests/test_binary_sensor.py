"""Tests for the ZONT connectivity sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.zont_ws.binary_sensor import (
    ZontCloudConnectedBinarySensor,
    ZontConnectedBinarySensor,
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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry


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
