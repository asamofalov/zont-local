"""Tests for ZONT user-element entities and commands."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.zont_local.binary_sensor import (
    async_setup_entry as async_setup_binary_sensor,
)
from custom_components.zont_local.button import async_setup_entry as async_setup_button
from custom_components.zont_local.const import DOMAIN
from custom_components.zont_local.coordinator import ZontDataUpdateCoordinator
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.entities.controller import ZontRestartButton
from custom_components.zont_local.entities.user_element import (
    ZontUserElementButton,
    ZontUserElementEntity,
    ZontUserElementStatusBinarySensor,
    ZontUserElementSwitch,
)
from custom_components.zont_local.protocol import (
    ZontClient,
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from custom_components.zont_local.protocol.heating_commands import (
    ZontCommandRejectedError,
    ZontCommandStateError,
)
from custom_components.zont_local.protocol.objects import (
    USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON,
    USER_ELEMENT_SUBTYPE_SIMPLE_BUTTON,
    USER_ELEMENT_SUBTYPE_STATUS,
    ZontUserElementData,
    immutable_objects,
)
from custom_components.zont_local.runtime import ZontRuntimeData
from custom_components.zont_local.switch import async_setup_entry as async_setup_switch
from custom_components.zont_local.user_element_control import (
    async_press_user_element,
    async_set_user_element_state_and_confirm,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

STATUS_ID = 10705
BUTTON_ID = 10676
SWITCH_ID = 10691


def _entry(
    *,
    status_state: int | float | None = 0,
    button_state: int | float | None = 255,
    switch_state: int | float | None = 0,
    available: bool = True,
) -> MockConfigEntry:
    """Build one config entry with all confirmed user-element subtypes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                STATUS_ID: ZontUserElementData(
                    STATUS_ID,
                    10,
                    "Статус входа",
                    available=available,
                    subtype=USER_ELEMENT_SUBTYPE_STATUS,
                    raw_state=status_state,
                    text="Замкнут" if status_state == 1 else "Разомкнут",
                ),
                BUTTON_ID: ZontUserElementData(
                    BUTTON_ID,
                    10,
                    "Поставить на охрану",
                    available=available,
                    subtype=USER_ELEMENT_SUBTYPE_SIMPLE_BUTTON,
                    raw_state=button_state,
                    text="Выполнить",
                ),
                SWITCH_ID: ZontUserElementData(
                    SWITCH_ID,
                    10,
                    "Режим охраны",
                    available=available,
                    subtype=USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON,
                    raw_state=switch_state,
                    text="Включено" if switch_state == 1 else "Выключено",
                ),
            }
        ),
    )
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.async_refresh_object = AsyncMock(return_value=True)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


@pytest.mark.parametrize(("raw_state", "is_on"), [(0, False), (1, True)])
def test_user_element_status_binary_sensor(raw_state: int, is_on: bool) -> None:
    entity = ZontUserElementStatusBinarySensor(
        _entry(status_state=raw_state),
        STATUS_ID,
    )

    assert entity.available
    assert entity.is_on is is_on
    assert entity.device_class is None
    assert entity.name is None
    assert entity.unique_id == "ABCDEF123456_10705_status"
    assert entity.suggested_object_id is None
    assert entity.extra_state_attributes == {
        "zont_text": "Замкнут" if is_on else "Разомкнут"
    }


def test_simple_user_element_button() -> None:
    entity = ZontUserElementButton(_entry(), BUTTON_ID)

    assert entity.available
    assert entity.name is None
    assert entity.unique_id == "ABCDEF123456_10676_button"
    assert entity.extra_state_attributes == {"zont_text": "Выполнить"}


@pytest.mark.parametrize(("raw_state", "is_on"), [(0, False), (1, True)])
def test_complex_user_element_switch(raw_state: int, is_on: bool) -> None:
    entity = ZontUserElementSwitch(
        _entry(switch_state=raw_state),
        SWITCH_ID,
    )

    assert entity.available
    assert entity.is_on is is_on
    assert entity.name is None
    assert entity.unique_id == "ABCDEF123456_10691_switch"
    assert entity.extra_state_attributes == {
        "zont_text": "Включено" if is_on else "Выключено"
    }


@pytest.mark.parametrize(
    "entity_factory",
    [
        lambda: ZontUserElementStatusBinarySensor(_entry(status_state=2), STATUS_ID),
        lambda: ZontUserElementButton(_entry(button_state=1), BUTTON_ID),
        lambda: ZontUserElementSwitch(_entry(switch_state=255), SWITCH_ID),
    ],
)
def test_user_element_entities_require_valid_available_state(
    entity_factory: Callable[[], ZontUserElementEntity],
) -> None:
    entity = entity_factory()

    assert not entity.available


async def test_simple_button_sends_press_command() -> None:
    entry = _entry()
    entity = ZontUserElementButton(entry, BUTTON_ID)

    with patch(
        "custom_components.zont_local.entities.user_element.async_press_user_element",
        new=AsyncMock(),
    ) as press:
        await entity.async_press()

    press.assert_awaited_once_with(entry.runtime_data.client, BUTTON_ID)


async def test_complex_switch_sends_both_states() -> None:
    entry = _entry()
    entity = ZontUserElementSwitch(entry, SWITCH_ID)

    with patch(
        "custom_components.zont_local.entities.user_element."
        "async_set_user_element_state_and_confirm",
        new=AsyncMock(),
    ) as set_state:
        await entity.async_turn_on()
        await entity.async_turn_off()

    assert set_state.await_args_list[0].args == (
        entry.runtime_data.client,
        entry.runtime_data.coordinator,
        SWITCH_ID,
        True,
    )
    assert set_state.await_args_list[1].args[-1] is False


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (ZontCommandRejectedError(1), "command_rejected"),
        (ZontCommandTimeoutError(), "command_timeout"),
        (ZontConnectionError(), "controller_offline"),
        (ZontProtocolError(), "protocol_error"),
    ],
)
async def test_simple_button_translates_command_errors(
    error: Exception,
    translation_key: str,
) -> None:
    entity = ZontUserElementButton(_entry(), BUTTON_ID)

    with (
        patch(
            "custom_components.zont_local.entities.user_element."
            "async_press_user_element",
            new=AsyncMock(side_effect=error),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_press()

    assert raised.value.translation_key == translation_key


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (ZontCommandRejectedError(1), "command_rejected"),
        (ZontCommandTimeoutError(), "command_timeout"),
        (ZontCommandStateError(), "user_element_state_not_confirmed"),
        (ZontConnectionError(), "controller_offline"),
        (ZontProtocolError(), "protocol_error"),
    ],
)
async def test_complex_switch_translates_command_errors(
    error: Exception,
    translation_key: str,
) -> None:
    entity = ZontUserElementSwitch(_entry(), SWITCH_ID)

    with (
        patch(
            "custom_components.zont_local.entities.user_element."
            "async_set_user_element_state_and_confirm",
            new=AsyncMock(side_effect=error),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_turn_on()

    assert raised.value.translation_key == translation_key


async def test_user_element_command_helpers_confirm_only_stateful_element() -> None:
    entry = _entry(switch_state=0)
    client = entry.runtime_data.client
    coordinator = entry.runtime_data.coordinator
    client.async_send_command = AsyncMock(return_value={"id": SWITCH_ID, "cmdres": 0})

    await async_press_user_element(client, BUTTON_ID)

    async def refresh(_object_id: int) -> bool:
        objects = dict(coordinator.data.objects)
        objects[SWITCH_ID] = ZontUserElementData(
            SWITCH_ID,
            10,
            "Режим охраны",
            subtype=USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON,
            raw_state=1,
            text="Включено",
        )
        coordinator.data = ZontData(
            controller=coordinator.data.controller,
            objects=immutable_objects(objects),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh
    await async_set_user_element_state_and_confirm(
        client,
        coordinator,
        SWITCH_ID,
        True,
    )

    assert client.async_send_command.await_args_list[0].args == (BUTTON_ID, 1)
    assert client.async_send_command.await_args_list[1].args == (SWITCH_ID, 1)
    coordinator.async_refresh_object.assert_awaited_once_with(SWITCH_ID)


async def test_user_element_command_rejection_and_unconfirmed_state() -> None:
    entry = _entry(switch_state=0)
    client = entry.runtime_data.client
    coordinator = entry.runtime_data.coordinator
    client.async_send_command = AsyncMock(return_value={"id": BUTTON_ID, "cmdres": 2})

    with pytest.raises(ZontCommandRejectedError):
        await async_press_user_element(client, BUTTON_ID)

    client.async_send_command.return_value = {"id": SWITCH_ID, "cmdres": 0}
    with pytest.raises(ZontCommandStateError):
        await async_set_user_element_state_and_confirm(
            client,
            coordinator,
            SWITCH_ID,
            True,
        )


async def test_platforms_add_confirmed_user_elements(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    async_add_binary_entities = MagicMock()
    async_add_button_entities = MagicMock()
    async_add_switch_entities = MagicMock()

    await async_setup_binary_sensor(hass, entry, async_add_binary_entities)
    await async_setup_button(hass, entry, async_add_button_entities)
    await async_setup_switch(hass, entry, async_add_switch_entities)

    binary_entities = [
        entity
        for invocation in async_add_binary_entities.call_args_list
        for entity in invocation.args[0]
    ]
    button_entities = [
        entity
        for invocation in async_add_button_entities.call_args_list
        for entity in invocation.args[0]
    ]
    switch_entities = [
        entity
        for invocation in async_add_switch_entities.call_args_list
        for entity in invocation.args[0]
    ]

    assert any(
        isinstance(entity, ZontUserElementStatusBinarySensor)
        for entity in binary_entities
    )
    assert any(isinstance(entity, ZontRestartButton) for entity in button_entities)
    assert any(isinstance(entity, ZontUserElementButton) for entity in button_entities)
    assert any(isinstance(entity, ZontUserElementSwitch) for entity in switch_entities)


async def test_subtype_change_reconciles_button_to_switch(
    hass: HomeAssistant,
) -> None:
    entry = _entry()
    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {BUTTON_ID: entry.runtime_data.coordinator.data.objects[BUTTON_ID]}
        ),
    )
    entry.add_to_hass(hass)
    async_add_button_entities = MagicMock()
    async_add_switch_entities = MagicMock()

    await async_setup_button(hass, entry, async_add_button_entities)
    await async_setup_switch(hass, entry, async_add_switch_entities)

    assert async_add_button_entities.call_count == 2
    assert async_add_switch_entities.call_count == 0

    entry.runtime_data.coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                BUTTON_ID: ZontUserElementData(
                    BUTTON_ID,
                    10,
                    "Поставить на охрану",
                    subtype=USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON,
                    raw_state=0,
                    text="Выключено",
                )
            }
        ),
    )
    await entry.runtime_data.object_entities.async_reconcile()

    added_switches = async_add_switch_entities.call_args.args[0]
    assert len(added_switches) == 1
    assert isinstance(added_switches[0], ZontUserElementSwitch)
    assert added_switches[0].unique_id == "ABCDEF123456_10676_switch"
