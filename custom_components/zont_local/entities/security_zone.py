"""Entities for ZONT security zones."""

from __future__ import annotations

import asyncio

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

from ..const import DOMAIN
from ..entity import ZontObjectCoordinatorEntity
from ..protocol import (
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from ..protocol.heating_commands import (
    ZontCommandRejectedError,
    ZontCommandStateError,
)
from ..protocol.objects import ZontSecurityZoneData
from ..runtime import ZontRuntimeData
from ..security_zone_control import async_set_security_zone_armed_and_confirm


class ZontSecurityZoneAlarmControlPanel(
    ZontObjectCoordinatorEntity,
    AlarmControlPanelEntity,
):
    """Represent one ZONT security zone."""

    _attr_name = None
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a ZONT security-zone panel."""
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "alarm_control_panel", None)

    @property
    def available(self) -> bool:
        """Return whether both confirmed zone-state fields are known."""
        zone = self._zone
        return (
            super().available
            and zone is not None
            and zone.armed is not None
            and zone.triggered is not None
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Map the observed ZONT state to a native alarm-panel state."""
        zone = self._zone
        if zone is None or zone.armed is None or zone.triggered is None:
            return None
        if zone.triggered:
            return AlarmControlPanelState.TRIGGERED
        if zone.armed:
            return AlarmControlPanelState.ARMED_AWAY
        return AlarmControlPanelState.DISARMED

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Put this ZONT zone on guard without a code."""
        await self._async_set_armed(True)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Remove this ZONT zone from guard without a code."""
        await self._async_set_armed(False)

    async def _async_set_armed(self, armed: bool) -> None:
        """Set and confirm one security-zone state with HA errors."""
        zone = self._zone
        if zone is not None and zone.armed is armed:
            return
        try:
            await async_set_security_zone_armed_and_confirm(
                self._client,
                self.coordinator,
                self._object_id,
                armed,
            )
        except asyncio.CancelledError:
            raise
        except ZontCommandRejectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontCommandTimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_timeout",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="controller_offline",
            ) from err
        except ZontCommandStateError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="security_zone_state_not_confirmed",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protocol_error",
            ) from err

    @property
    def _zone(self) -> ZontSecurityZoneData | None:
        """Return the current security-zone snapshot."""
        obj = self.object_data
        return obj if isinstance(obj, ZontSecurityZoneData) else None


class ZontSecurityZoneAlarmBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the alarm flag of one ZONT security zone."""

    _attr_translation_key = "security_zone_alarm"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a ZONT security-zone alarm sensor."""
        super().__init__(entry, object_id, "triggered", "alarm")

    @property
    def available(self) -> bool:
        """Return whether the alarm flag is known."""
        zone = self._zone
        return super().available and zone is not None and zone.triggered is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the zone is reporting an alarm."""
        zone = self._zone
        return zone.triggered if zone is not None else None

    @property
    def _zone(self) -> ZontSecurityZoneData | None:
        """Return the current security-zone snapshot."""
        obj = self.object_data
        return obj if isinstance(obj, ZontSecurityZoneData) else None
