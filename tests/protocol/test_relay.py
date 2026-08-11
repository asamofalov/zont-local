"""Tests for ZONT relay configuration, diagnostics, and commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_local.coordinator import (
    ZontDataUpdateCoordinator,
)
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.protocol import ZontClient
from custom_components.zont_local.protocol.heating_commands import (
    ZontCommandRejectedError,
    ZontCommandStateError,
)
from custom_components.zont_local.protocol.objects import (
    ZontRelayData,
    immutable_objects,
)
from custom_components.zont_local.protocol.relay import (
    ZontRelayConfiguration,
    ZontRelayInternalState,
    ZontRelayParseError,
    immutable_relay_configurations,
    immutable_relay_states,
    parse_relay_configuration,
    parse_relay_internal_state,
    relay_logical_state,
)
from custom_components.zont_local.relay_control import (
    async_set_relay_state_and_confirm,
)


@pytest.mark.parametrize(
    ("register", "inverse"),
    [(0, False), (1, True), (8, True), (9, True)],
)
def test_parse_relay_configuration(register: int, inverse: bool) -> None:
    configuration = parse_relay_configuration(
        f"#Z20488:14,'Реле, котла',255,{register}",
        20488,
    )

    assert configuration == ZontRelayConfiguration(20488, register)
    assert configuration.is_inverse is inverse


@pytest.mark.parametrize(
    "response",
    [
        "",
        "#Z20488:15,'Реле',255,9",
        "#Z20488:14,'Реле',255",
        "#Z20488:14,,255,9",
        "#Z20488:14,'Реле',-1,9",
        "#Z20488:14,'Реле',255,-1",
        "#Zbad:14,'Реле',255,9",
        "x" * 257,
    ],
)
def test_invalid_relay_configuration_is_rejected(response: str) -> None:
    with pytest.raises(ZontRelayParseError):
        parse_relay_configuration(response)


def test_relay_configuration_validates_expected_id() -> None:
    with pytest.raises(ZontRelayParseError):
        parse_relay_configuration("#Z20488:14,'Реле',255,9", 20489)


def test_parse_relay_internal_state_exposes_known_flags() -> None:
    state = parse_relay_internal_state(" #Y20488$15 ", 20488)

    assert state == ZontRelayInternalState(20488, 15)
    assert state.is_active
    assert state.has_failed
    assert state.is_test_mode
    assert state.is_test_pending


@pytest.mark.parametrize(
    "response",
    ["", "#Y20488$-1", "#Y20488$1,2", "#Z20488:0", "x" * 257],
)
def test_invalid_relay_internal_state_is_rejected(response: str) -> None:
    with pytest.raises(ZontRelayParseError):
        parse_relay_internal_state(response)


def test_relay_internal_state_validates_expected_id() -> None:
    with pytest.raises(ZontRelayParseError):
        parse_relay_internal_state("#Y20488$0", 20489)


@pytest.mark.parametrize(
    ("physical", "inverse", "logical"),
    [
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, False),
    ],
)
def test_relay_logical_state(
    physical: bool,
    inverse: bool,
    logical: bool,
) -> None:
    relay = ZontRelayData(20488, 14, "Реле", output_active=physical)
    configuration = ZontRelayConfiguration(20488, 9 if inverse else 0)

    assert relay_logical_state(relay, configuration) is logical


def test_relay_mappings_are_immutable() -> None:
    configuration = ZontRelayConfiguration(20488, 0)
    state = ZontRelayInternalState(20488, 0)

    configurations = immutable_relay_configurations({20488: configuration})
    states = immutable_relay_states({20488: state})

    with pytest.raises(TypeError):
        configurations[20488] = configuration  # type: ignore[index]
    with pytest.raises(TypeError):
        states[20488] = state  # type: ignore[index]


async def test_set_relay_state_confirms_logical_state() -> None:
    client = MagicMock(spec=ZontClient)
    client.async_send_command = AsyncMock(return_value={"id": 20488, "cmdres": 0})
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {20488: ZontRelayData(20488, 14, "Реле", output_active=False)}
        ),
        relay_configurations=immutable_relay_configurations(
            {20488: ZontRelayConfiguration(20488, 9)}
        ),
    )

    async def refresh(_: int) -> bool:
        coordinator.data = ZontData(
            controller=ZontControllerData(info=None),
            objects=immutable_objects(
                {20488: ZontRelayData(20488, 14, "Реле", output_active=False)}
            ),
            relay_configurations=immutable_relay_configurations(
                {20488: ZontRelayConfiguration(20488, 9)}
            ),
        )
        return True

    coordinator.async_refresh_object = AsyncMock(side_effect=refresh)

    await async_set_relay_state_and_confirm(client, coordinator, 20488, True)

    client.async_send_command.assert_awaited_once_with(20488, 1)
    coordinator.async_refresh_object.assert_awaited_once_with(20488)


async def test_set_relay_state_rejects_controller_error() -> None:
    client = MagicMock(spec=ZontClient)
    client.async_send_command = AsyncMock(return_value={"id": 20488, "cmdres": 1})
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)

    with pytest.raises(ZontCommandRejectedError):
        await async_set_relay_state_and_confirm(client, coordinator, 20488, False)

    client.async_send_command.assert_awaited_once_with(20488, 0)


async def test_set_relay_state_rejects_unconfirmed_state() -> None:
    client = MagicMock(spec=ZontClient)
    client.async_send_command = AsyncMock(return_value={"id": 20488, "cmdres": 0})
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.async_refresh_object = AsyncMock(return_value=False)

    with pytest.raises(ZontCommandStateError):
        await async_set_relay_state_and_confirm(client, coordinator, 20488, True)


async def test_set_relay_state_rejects_mismatched_logical_state() -> None:
    client = MagicMock(spec=ZontClient)
    client.async_send_command = AsyncMock(return_value={"id": 20488, "cmdres": 0})
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.async_refresh_object = AsyncMock(return_value=True)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {20488: ZontRelayData(20488, 14, "Реле", output_active=False)}
        ),
        relay_configurations=immutable_relay_configurations(
            {20488: ZontRelayConfiguration(20488, 0)}
        ),
    )

    with pytest.raises(ZontCommandStateError):
        await async_set_relay_state_and_confirm(client, coordinator, 20488, True)
