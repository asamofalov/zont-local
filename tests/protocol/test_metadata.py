"""Tests for cached ZONT object metadata refreshers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

from custom_components.zont_local.protocol import ZontClient
from custom_components.zont_local.protocol.heating_config import (
    ZontHeatingModeConfiguration,
    immutable_heating_modes,
)
from custom_components.zont_local.protocol.metadata import (
    ZontHeatingMetadataRefresher,
    ZontRelayMetadataRefresher,
)
from custom_components.zont_local.protocol.objects import (
    ZontHeatingCircuitData,
    ZontRelayData,
    immutable_objects,
)


def _client() -> MagicMock:
    """Return a protocol client mock with asynchronous request methods."""
    client = MagicMock(spec=ZontClient)
    client.async_get_object_ids = AsyncMock()
    client.async_send_system_command = AsyncMock()
    return client


async def test_heating_mode_ids_are_checked_only_when_configuration_is_stale() -> None:
    """Mode discovery must follow the static-configuration refresh policy."""
    client = _client()
    client.async_get_object_ids.side_effect = [[20501], [20502]]
    client.async_send_system_command.side_effect = [
        "#Z20501:20,'Комфорт',[8362],[3330],[0],21,[0],10,0,10,1,0",
        "#Y8362$3330,3330,[],0,0,20501,4097,0,[20501],0,0",
        "#Y8362$3330,3330,[],0,0,20501,4097,0,[20501],0,0",
        "#Z20502:20,'Эконом',[8362],[3100],[0],11,[0],10,0,10,2,0",
        "#Y8362$3330,3100,[],0,0,20502,4097,0,[20502],0,0",
    ]
    refresher = ZontHeatingMetadataRefresher(client)
    objects = immutable_objects(
        {8362: ZontHeatingCircuitData(8362, 16, "ГВС", subtype=1)}
    )

    _, _, modes = await refresher.async_refresh(objects, immutable_heating_modes())
    _, _, unchanged_modes = await refresher.async_refresh(objects, modes)
    refresher.mark_stale()
    _, _, changed_modes = await refresher.async_refresh(objects, unchanged_modes)

    assert modes[20501].name == "Комфорт"
    assert unchanged_modes is modes
    assert set(changed_modes) == {20502}
    assert changed_modes[20502].name == "Эконом"
    assert client.async_get_object_ids.await_args_list == [call(20), call(20)]
    commands = [
        item.args[0] for item in client.async_send_system_command.await_args_list
    ]
    assert commands.count("#Z20501?") == 1
    assert commands.count("#Z20502?") == 1
    assert commands.count("#Y8362?") == 3


async def test_incomplete_heating_mode_refresh_keeps_previous_set() -> None:
    """One invalid #Z response must not publish a partial mode map."""
    client = _client()
    client.async_get_object_ids.return_value = [20501, 20502]
    client.async_send_system_command.side_effect = [
        "#Z20501:20,'Новый комфорт',[8362],[3330],[0],21,[0],10,0,10,1,0",
        "#Z20502:!",
        "#Y8362$3330,3330,[],0,0,20501,4097,0,[20501,20502],0,0",
    ]
    refresher = ZontHeatingMetadataRefresher(client)
    previous_modes = immutable_heating_modes(
        {
            20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330}),
            20502: ZontHeatingModeConfiguration(20502, "Эконом", {8362: 3100}),
        }
    )
    objects = immutable_objects(
        {8362: ZontHeatingCircuitData(8362, 16, "ГВС", subtype=1)}
    )

    _, _, modes = await refresher.async_refresh(objects, previous_modes)

    assert modes is previous_modes
    assert modes[20501].name == "Комфорт"
    assert refresher.refresh_needed


async def test_incomplete_relay_refresh_keeps_previous_configuration() -> None:
    """An invalid periodic #Z response must preserve known relay inversion."""
    client = _client()
    client.async_send_system_command.side_effect = [
        "#Z20488:14,'Реле',255,9",
        "#Y20488$1",
        "#Z20488:!",
        "#Y20488$1",
    ]
    refresher = ZontRelayMetadataRefresher(client)
    objects = immutable_objects(
        {20488: ZontRelayData(20488, 14, "Реле", output_active=True)}
    )

    configurations, _ = await refresher.async_refresh(objects)
    refresher.mark_stale()
    retained_configurations, _ = await refresher.async_refresh(objects)

    assert configurations[20488].is_inverse
    assert retained_configurations[20488] == configurations[20488]
    assert refresher.refresh_needed
