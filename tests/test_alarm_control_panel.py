"""Tests for ZONT security-zone entities and control."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.zont_local.alarm_control_panel import (
    async_setup_entry as async_setup_alarm_control_panel,
)
from custom_components.zont_local.binary_sensor import (
    async_setup_entry as async_setup_binary_sensor,
)
from custom_components.zont_local.const import DOMAIN
from custom_components.zont_local.coordinator import ZontDataUpdateCoordinator
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.entities.security_zone import (
    ZontSecurityZoneAlarmBinarySensor,
    ZontSecurityZoneAlarmControlPanel,
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
    ZontSecurityZoneData,
    immutable_objects,
)
from custom_components.zont_local.runtime import ZontRuntimeData
from custom_components.zont_local.security_zone_control import (
    async_set_security_zone_armed_and_confirm,
)
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

ZONE_ID = 10657


def _entry(
    *,
    armed: bool | None = False,
    triggered: bool | None = False,
    available: bool = True,
) -> MockConfigEntry:
    """Build one config entry with a security-zone snapshot."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
    )
    client = MagicMock(spec=ZontClient)
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                ZONE_ID: ZontSecurityZoneData(
                    ZONE_ID,
                    2,
                    "Тестовая зона",
                    available=available,
                    armed=armed,
                    triggered=triggered,
                )
            }
        ),
    )
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.async_refresh_object = AsyncMock(return_value=True)
    entry.runtime_data = ZontRuntimeData(client, coordinator)
    return entry


@pytest.mark.parametrize(
    ("armed", "triggered", "expected"),
    [
        (False, False, AlarmControlPanelState.DISARMED),
        (True, False, AlarmControlPanelState.ARMED_AWAY),
        (True, True, AlarmControlPanelState.TRIGGERED),
        (False, True, AlarmControlPanelState.TRIGGERED),
    ],
)
def test_security_zone_maps_alarm_state(
    armed: bool,
    triggered: bool,
    expected: AlarmControlPanelState,
) -> None:
    entity = ZontSecurityZoneAlarmControlPanel(
        _entry(armed=armed, triggered=triggered),
        ZONE_ID,
    )

    assert entity.available
    assert entity.alarm_state is expected
    assert entity.supported_features is AlarmControlPanelEntityFeature.ARM_AWAY
    assert not entity.code_arm_required
    assert entity.name is None
    assert entity.unique_id == "ABCDEF123456_10657_alarm_control_panel"


@pytest.mark.parametrize(
    ("armed", "triggered", "available"),
    [(None, False, True), (False, None, True), (False, False, False)],
)
def test_security_zone_panel_requires_complete_available_state(
    armed: bool | None,
    triggered: bool | None,
    available: bool,
) -> None:
    entity = ZontSecurityZoneAlarmControlPanel(
        _entry(armed=armed, triggered=triggered, available=available),
        ZONE_ID,
    )

    assert not entity.available


def test_security_zone_alarm_binary_sensor() -> None:
    entity = ZontSecurityZoneAlarmBinarySensor(
        _entry(armed=True, triggered=True),
        ZONE_ID,
    )

    assert entity.available
    assert entity.is_on
    assert entity.device_class is BinarySensorDeviceClass.SAFETY
    assert entity.translation_key == "security_zone_alarm"
    assert entity.unique_id == "ABCDEF123456_10657_triggered"
    assert entity.suggested_object_id == "alarm"


async def test_security_zone_panel_sends_confirmed_commands() -> None:
    arm_entry = _entry(armed=False, triggered=True)
    disarm_entry = _entry(armed=True, triggered=False)
    arm_entity = ZontSecurityZoneAlarmControlPanel(arm_entry, ZONE_ID)
    disarm_entity = ZontSecurityZoneAlarmControlPanel(disarm_entry, ZONE_ID)

    with patch(
        "custom_components.zont_local.entities.security_zone."
        "async_set_security_zone_armed_and_confirm",
        new=AsyncMock(),
    ) as set_armed:
        await arm_entity.async_alarm_arm_away()
        await disarm_entity.async_alarm_disarm()

    assert set_armed.await_args_list[0].args == (
        arm_entry.runtime_data.client,
        arm_entry.runtime_data.coordinator,
        ZONE_ID,
        True,
    )
    assert set_armed.await_args_list[1].args[-1] is False


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (ZontCommandRejectedError(1), "command_rejected"),
        (ZontCommandTimeoutError(), "command_timeout"),
        (ZontCommandStateError(), "security_zone_state_not_confirmed"),
        (ZontConnectionError(), "controller_offline"),
        (ZontProtocolError(), "protocol_error"),
    ],
)
async def test_security_zone_panel_translates_command_errors(
    error: Exception,
    translation_key: str,
) -> None:
    entity = ZontSecurityZoneAlarmControlPanel(_entry(), ZONE_ID)

    with (
        patch(
            "custom_components.zont_local.entities.security_zone."
            "async_set_security_zone_armed_and_confirm",
            new=AsyncMock(side_effect=error),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_alarm_arm_away()

    assert raised.value.translation_key == translation_key


async def test_security_zone_control_confirms_armed_state() -> None:
    entry = _entry(armed=False, triggered=True)
    client = entry.runtime_data.client
    coordinator = entry.runtime_data.coordinator
    client.async_send_command = AsyncMock(return_value={"id": ZONE_ID, "cmdres": 0})

    async def refresh(_object_id: int) -> bool:
        coordinator.data = ZontData(
            controller=coordinator.data.controller,
            objects=immutable_objects(
                {
                    ZONE_ID: ZontSecurityZoneData(
                        ZONE_ID,
                        2,
                        "Тестовая зона",
                        armed=True,
                        triggered=True,
                    )
                }
            ),
        )
        return True

    coordinator.async_refresh_object.side_effect = refresh

    await async_set_security_zone_armed_and_confirm(
        client,
        coordinator,
        ZONE_ID,
        True,
    )

    client.async_send_command.assert_awaited_once_with(ZONE_ID, 1)
    coordinator.async_refresh_object.assert_awaited_once_with(ZONE_ID)


async def test_security_zone_control_rejects_unconfirmed_state() -> None:
    entry = _entry(armed=False)
    client = entry.runtime_data.client
    coordinator = entry.runtime_data.coordinator
    client.async_send_command = AsyncMock(return_value={"id": ZONE_ID, "cmdres": 0})

    with pytest.raises(ZontCommandStateError):
        await async_set_security_zone_armed_and_confirm(
            client,
            coordinator,
            ZONE_ID,
            True,
        )


async def test_setup_adds_security_zone_entities(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    async_add_alarm_entities = MagicMock()
    async_add_binary_entities = MagicMock()

    await async_setup_alarm_control_panel(hass, entry, async_add_alarm_entities)
    await async_setup_binary_sensor(hass, entry, async_add_binary_entities)

    alarms = async_add_alarm_entities.call_args.args[0]
    assert len(alarms) == 1
    assert isinstance(alarms[0], ZontSecurityZoneAlarmControlPanel)

    binary_entities = [
        entity
        for invocation in async_add_binary_entities.call_args_list
        for entity in invocation.args[0]
    ]
    assert any(
        isinstance(entity, ZontSecurityZoneAlarmBinarySensor)
        for entity in binary_entities
    )
