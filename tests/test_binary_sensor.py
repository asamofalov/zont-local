"""Tests for the ZONT connectivity sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.zont_ws.binary_sensor import ZontConnectedBinarySensor
from custom_components.zont_ws.client import ZontWsClient
from custom_components.zont_ws.const import DOMAIN, connection_signal
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_connection_state_updates(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    client = MagicMock(spec=ZontWsClient)
    client.is_connected = True
    entry.runtime_data = client
    entity = ZontConnectedBinarySensor(entry)
    entity.hass = hass
    entity.entity_id = "binary_sensor.zont_connected"
    entity.async_write_ha_state = MagicMock()

    assert entity.is_on
    assert entity.unique_id == f"{entry.entry_id}_connected"
    assert entity.device_info["identifiers"] == {(DOMAIN, entry.entry_id)}
    await entity.async_added_to_hass()
    async_dispatcher_send(hass, connection_signal(entry.entry_id), False)

    assert not entity.is_on
    entity.async_write_ha_state.assert_called_once()
    await entity.async_remove()
