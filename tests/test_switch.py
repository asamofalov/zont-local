"""Tests for ZONT relay switches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.zont_ws.client import (
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
    ZontWsClient,
)
from custom_components.zont_ws.const import DOMAIN
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
    ZontDataUpdateCoordinator,
    ZontRuntimeData,
)
from custom_components.zont_ws.heating import (
    ZontCommandRejectedError,
    ZontCommandStateError,
)
from custom_components.zont_ws.objects import ZontRelayData, immutable_objects
from custom_components.zont_ws.relay import (
    ZontRelayConfiguration,
    immutable_relay_configurations,
)
from custom_components.zont_ws.switch import (
    ZontRelaySwitch,
    async_setup_entry,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(
    *,
    output_active: bool | None = True,
    setting_register: int | None = 0,
    available: bool = True,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontWsClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    relay = ZontRelayData(
        20488,
        14,
        "Реле",
        available=available,
        output_active=output_active,
    )
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({20488: relay}),
        relay_configurations=immutable_relay_configurations(
            {20488: ZontRelayConfiguration(20488, setting_register)}
            if setting_register is not None
            else None
        ),
    )
    coordinator.async_add_listener.return_value = lambda: None
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


@pytest.mark.parametrize(
    ("output_active", "setting_register", "is_on"),
    [(False, 0, False), (True, 0, True), (False, 1, True), (True, 8, False)],
)
def test_relay_switch_maps_logical_state(
    output_active: bool,
    setting_register: int,
    is_on: bool,
) -> None:
    entity = ZontRelaySwitch(
        _entry(
            output_active=output_active,
            setting_register=setting_register,
        ),
        20488,
    )

    assert entity.available
    assert entity.is_on is is_on
    assert entity.name is None
    assert entity.unique_id == "ABCDEF123456_20488_switch"
    assert entity.suggested_object_id is None


@pytest.mark.parametrize(
    ("output_active", "setting_register", "object_available"),
    [(None, 0, True), (True, None, True), (True, 0, False)],
)
def test_relay_switch_requires_all_state(
    output_active: bool | None,
    setting_register: int | None,
    object_available: bool,
) -> None:
    entity = ZontRelaySwitch(
        _entry(
            output_active=output_active,
            setting_register=setting_register,
            available=object_available,
        ),
        20488,
    )

    assert not entity.available


async def test_relay_switch_sends_logical_commands() -> None:
    entry = _entry()
    entity = ZontRelaySwitch(entry, 20488)

    with patch(
        "custom_components.zont_ws.switch.async_set_relay_state_and_confirm",
        new=AsyncMock(),
    ) as set_state:
        await entity.async_turn_on()
        await entity.async_turn_off()

    assert set_state.await_args_list[0].args == (
        entry.runtime_data.client,
        entry.runtime_data.coordinator,
        20488,
        True,
    )
    assert set_state.await_args_list[1].args[-1] is False


async def test_relay_switch_rejects_control_without_configuration() -> None:
    entity = ZontRelaySwitch(_entry(setting_register=None), 20488)

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_turn_on()

    assert raised.value.translation_key == "relay_control_unavailable"


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (ZontCommandRejectedError(1), "command_rejected"),
        (ZontCommandTimeoutError(), "command_timeout"),
        (ZontCommandStateError(), "relay_state_not_confirmed"),
        (ZontConnectionError(), "controller_offline"),
        (ZontProtocolError(), "protocol_error"),
    ],
)
async def test_relay_switch_translates_command_errors(
    error: Exception,
    translation_key: str,
) -> None:
    entity = ZontRelaySwitch(_entry(), 20488)

    with (
        patch(
            "custom_components.zont_ws.switch.async_set_relay_state_and_confirm",
            new=AsyncMock(side_effect=error),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_turn_on()

    assert raised.value.translation_key == translation_key


async def test_setup_adds_relay_switch_once(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], ZontRelaySwitch)

    listener = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
    listener()
    assert async_add_entities.call_count == 1
