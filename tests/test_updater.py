"""Tests for the complete ZONT data snapshot updater."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.protocol import (
    ZontClient,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
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
from custom_components.zont_local.updater import ZontDataUpdater, _ObjectRefreshResult


def _runtime_updater(
    client: MagicMock,
    monotonic_time: MagicMock,
) -> ZontDataUpdater:
    """Return an updater with non-object sources isolated for catalog tests."""
    updater = ZontDataUpdater(client, None, MagicMock(), monotonic_time)
    updater._async_refresh_info = AsyncMock(return_value=None)
    updater._async_refresh_server_status = AsyncMock(return_value=None)
    updater._async_refresh_power_status = AsyncMock(return_value=None)
    updater._async_refresh_wifi_status = AsyncMock(return_value=None)
    updater._async_refresh_ethernet_status = AsyncMock(return_value=None)
    updater._async_refresh_gsm_status = AsyncMock(return_value=None)
    updater.mixer_metadata.async_refresh = AsyncMock(
        return_value=immutable_mixer_states()
    )
    updater.relay_metadata.async_refresh = AsyncMock(
        return_value=(immutable_relay_configurations(), immutable_relay_states())
    )
    updater.relay_metadata._relay_configuration_refresh_needed = False
    updater.heating_metadata.async_refresh = AsyncMock(
        return_value=(
            immutable_heating_controls(),
            immutable_heating_states(),
            immutable_heating_modes(),
        )
    )
    updater.heating_metadata._heating_configuration_refresh_needed = False
    return updater


def _adapter_state(temperature: float) -> dict[str, object]:
    """Return one supported object state for catalog tests."""
    return {
        "id": 1001,
        "type": 6,
        "name": "Котёл",
        "water": temperature,
    }


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
    updater._async_refresh_objects = AsyncMock(
        return_value=_ObjectRefreshResult(
            immutable_objects(),
            frozenset(),
            frozenset(),
            False,
        )
    )
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


async def test_full_catalog_classifies_unknown_ids_only_on_static_refresh() -> None:
    """Unsupported IDs must not add state requests to every control poll."""
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_ids = AsyncMock(
        side_effect=[[1001, 9000], [1001, 9000], [1001, 9000]]
    )
    client.async_get_object_state = AsyncMock(
        side_effect=[
            _adapter_state(35),
            {"id": 9000, "type": 99, "name": "Не поддерживается"},
            _adapter_state(36),
            _adapter_state(37),
            {"id": 9000, "type": 99, "name": "Не поддерживается"},
        ]
    )
    now = MagicMock(side_effect=[100.0, 101.0, 1000.0])
    updater = _runtime_updater(client, now)
    data = ZontData(controller=ZontControllerData(info=None))

    data = await updater.async_refresh(data)
    updater._next_configuration_refresh_at = 1000.0
    updater.heating_metadata._heating_configuration_refresh_needed = False
    updater.relay_metadata._relay_configuration_refresh_needed = False
    data = await updater.async_refresh(data)
    data = await updater.async_refresh(data)

    assert data.objects[1001].available
    assert client.async_get_object_ids.await_args_list == [
        call(255),
        call(255),
        call(255),
    ]
    assert client.async_get_object_state.await_args_list == [
        call(1001),
        call(9000),
        call(1001),
        call(1001),
        call(9000),
    ]


async def test_successful_catalog_removes_missing_object() -> None:
    """Only a confirmed catalog change may remove an existing object."""
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_ids = AsyncMock(side_effect=[[1001], []])
    client.async_get_object_state = AsyncMock(return_value=_adapter_state(35))
    updater = _runtime_updater(client, MagicMock(side_effect=[100.0, 101.0]))
    data = ZontData(controller=ZontControllerData(info=None))

    data = await updater.async_refresh(data)
    data = await updater.async_refresh(data)

    assert not data.objects
    client.async_get_object_state.assert_awaited_once_with(1001)


async def test_invalid_catalog_preserves_and_refreshes_known_object() -> None:
    """An invalid catalog response must not be interpreted as object removal."""
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_ids = AsyncMock(
        side_effect=[[1001], ZontProtocolError("invalid catalog")]
    )
    client.async_get_object_state = AsyncMock(
        side_effect=[_adapter_state(35), _adapter_state(36)]
    )
    updater = _runtime_updater(client, MagicMock(side_effect=[100.0, 101.0]))
    data = ZontData(controller=ZontControllerData(info=None))

    data = await updater.async_refresh(data)
    data = await updater.async_refresh(data)

    assert data.objects[1001].available
    assert data.objects[1001].flow_temperature == 36
    assert updater._object_catalog_ids == frozenset({1001})


async def test_catalog_is_committed_only_with_complete_snapshot() -> None:
    """A later source failure must leave new IDs eligible for classification."""
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.async_get_object_ids = AsyncMock(side_effect=[[1001], [1001]])
    client.async_get_object_state = AsyncMock(
        side_effect=[_adapter_state(35), _adapter_state(36)]
    )
    updater = _runtime_updater(client, MagicMock(side_effect=[100.0, 101.0]))
    updater.mixer_metadata.async_refresh.side_effect = [
        ZontRequestTimeoutError,
        immutable_mixer_states(),
    ]
    data = ZontData(controller=ZontControllerData(info=None))

    with pytest.raises(ZontRequestTimeoutError):
        await updater.async_refresh(data)
    data = await updater.async_refresh(data)

    assert data.objects[1001].flow_temperature == 36
    assert updater._object_catalog_ids == frozenset({1001})
    assert client.async_get_object_state.await_count == 2
