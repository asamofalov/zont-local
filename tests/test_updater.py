"""Tests for the complete ZONT data snapshot updater."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.protocol import ZontClient
from custom_components.zont_local.protocol.heating_config import (
    immutable_heating_controls,
    immutable_heating_modes,
    immutable_heating_states,
)
from custom_components.zont_local.protocol.mixer import immutable_mixer_states
from custom_components.zont_local.protocol.objects import immutable_objects
from custom_components.zont_local.protocol.relay import (
    immutable_relay_configurations,
    immutable_relay_states,
)
from custom_components.zont_local.updater import ZontDataUpdater


async def test_configuration_is_refreshed_every_fifteen_minutes() -> None:
    """Cached #Z sources must be marked stale only when their deadline is due."""
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    now = 100.0
    updater = ZontDataUpdater(client, None, MagicMock(), lambda: now)
    updater._async_refresh_info = AsyncMock(return_value=None)
    updater._async_refresh_server_status = AsyncMock(return_value=None)
    updater._async_refresh_power_status = AsyncMock(return_value=None)
    updater._async_refresh_wifi_status = AsyncMock(return_value=None)
    updater._async_refresh_ethernet_status = AsyncMock(return_value=None)
    updater._async_refresh_gsm_status = AsyncMock(return_value=None)
    updater._async_refresh_objects = AsyncMock(return_value=immutable_objects())
    updater.mixer_metadata.async_refresh = AsyncMock(
        return_value=immutable_mixer_states()
    )
    updater.relay_metadata.mark_stale = MagicMock()
    updater.relay_metadata.async_refresh = AsyncMock(
        return_value=(immutable_relay_configurations(), immutable_relay_states())
    )
    updater.relay_metadata._relay_configuration_refresh_needed = False
    updater.heating_metadata.mark_stale = MagicMock()
    updater.heating_metadata.async_refresh = AsyncMock(
        return_value=(
            immutable_heating_controls(),
            immutable_heating_states(),
            immutable_heating_modes(),
        )
    )
    updater.heating_metadata._heating_configuration_refresh_needed = False
    previous = ZontData(controller=ZontControllerData(info=None))

    await updater.async_refresh(previous)
    now += 899
    await updater.async_refresh(previous)
    now += 1
    await updater.async_refresh(previous)

    assert updater.heating_metadata.mark_stale.call_count == 2
    assert updater.relay_metadata.mark_stale.call_count == 2


async def test_reconnect_resets_configuration_refresh_deadline() -> None:
    """Reconnect must force configuration refresh before the periodic deadline."""
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    now = 100.0
    updater = ZontDataUpdater(client, None, MagicMock(), lambda: now)
    updater._next_configuration_refresh_at = now + 900
    updater.heating_metadata.mark_stale = MagicMock()
    updater.relay_metadata.mark_stale = MagicMock()

    updater.mark_connection_stale()

    assert updater._next_configuration_refresh_at is None
    updater.heating_metadata.mark_stale.assert_called_once_with()
    updater.relay_metadata.mark_stale.assert_called_once_with()
