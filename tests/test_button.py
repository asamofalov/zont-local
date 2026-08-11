"""Tests for the ZONT restart button."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.zont_local.const import DOMAIN, connection_signal
from custom_components.zont_local.entities.controller import ZontRestartButton
from custom_components.zont_local.protocol import ZontClient, ZontConnectionError
from custom_components.zont_local.runtime import ZontRuntimeData
from homeassistant.components.button import ButtonDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry() -> tuple[MockConfigEntry, MagicMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    entry.runtime_data = ZontRuntimeData(client, MagicMock())
    return entry, client


async def test_restart_button_tracks_connection(hass: HomeAssistant) -> None:
    entry, _ = _entry()
    entity = ZontRestartButton(entry)
    entity.hass = hass
    entity.entity_id = "button.zont_restart"
    entity.async_write_ha_state = MagicMock()

    assert entity.available
    assert entity.device_class is ButtonDeviceClass.RESTART
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.unique_id == "ABCDEF123456_restart"
    assert entity.suggested_object_id == "restart"

    await entity.async_added_to_hass()
    async_dispatcher_send(hass, connection_signal(entry.entry_id), False)

    assert not entity.available
    entity.async_write_ha_state.assert_called_once()


async def test_restart_button_sends_controller_command(hass: HomeAssistant) -> None:
    entry, client = _entry()
    entity = ZontRestartButton(entry)
    entity.hass = hass

    with patch(
        "custom_components.zont_local.entities.controller.async_restart_controller",
        new=AsyncMock(),
    ) as restart:
        await entity.async_press()

    restart.assert_awaited_once_with(client)


async def test_restart_button_translates_connection_error(
    hass: HomeAssistant,
) -> None:
    entry, _ = _entry()
    entity = ZontRestartButton(entry)
    entity.hass = hass

    with (
        patch(
            "custom_components.zont_local.entities.controller.async_restart_controller",
            new=AsyncMock(side_effect=ZontConnectionError),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_press()

    assert raised.value.translation_key == "controller_offline"
