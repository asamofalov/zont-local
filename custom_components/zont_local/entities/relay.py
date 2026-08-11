"""Entities for ZONT relays."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..const import DOMAIN
from ..entity import ZontObjectCoordinatorEntity
from ..protocol import (
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from ..protocol.heating_commands import ZontCommandRejectedError, ZontCommandStateError
from ..protocol.objects import ZontRelayData
from ..protocol.relay import (
    ZontRelayConfiguration,
    ZontRelayInternalState,
    relay_logical_state,
)
from ..relay_control import async_set_relay_state_and_confirm
from ..runtime import ZontRuntimeData


class ZontRelaySwitch(ZontObjectCoordinatorEntity, SwitchEntity):
    """Represent the logical state of one ZONT relay."""

    _attr_name = None

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a ZONT relay switch."""
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "switch", None)

    @property
    def available(self) -> bool:
        """Return whether output state and inversion configuration are known."""
        return (
            super().available
            and self._relay is not None
            and self._configuration is not None
            and self.is_on is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the logical relay state with output inversion applied."""
        relay = self._relay
        configuration = self._configuration
        if relay is None or configuration is None:
            return None
        return relay_logical_state(relay, configuration)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the relay on and confirm the observed state."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the relay off and confirm the observed state."""
        await self._async_set_state(False)

    async def _async_set_state(self, is_on: bool) -> None:
        """Set one relay state and translate protocol failures for HA."""
        if self._configuration is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="relay_control_unavailable",
            )
        try:
            await async_set_relay_state_and_confirm(
                self._client,
                self.coordinator,
                self._object_id,
                is_on,
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
                translation_key="relay_state_not_confirmed",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protocol_error",
            ) from err

    @property
    def _relay(self) -> ZontRelayData | None:
        """Return the current relay object snapshot."""
        obj = self.object_data
        return obj if isinstance(obj, ZontRelayData) else None

    @property
    def _configuration(self) -> ZontRelayConfiguration | None:
        """Return the current inversion configuration."""
        return self.coordinator.data.relay_configurations.get(self._object_id)


class ZontRelayFailedBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the internal failure flag of one relay."""

    _attr_translation_key = "relay_failed"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a relay failure sensor."""
        super().__init__(entry, object_id, "failed", "failed")

    @property
    def available(self) -> bool:
        """Return whether internal relay flags have been read."""
        return super().available and self._internal_state is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the relay reports a failure."""
        state = self._internal_state
        return state.has_failed if state is not None else None

    @property
    def _internal_state(self) -> ZontRelayInternalState | None:
        """Return the current internal state of this relay."""
        obj = self.object_data
        if not isinstance(obj, ZontRelayData):
            return None
        return self.coordinator.data.relay_states.get(self._object_id)
