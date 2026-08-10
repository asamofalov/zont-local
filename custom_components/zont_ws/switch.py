"""Switch entities for ZONT relays."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import (
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from .const import DOMAIN
from .coordinator import ZontRuntimeData
from .entity import ZontObjectCoordinatorEntity
from .heating import ZontCommandRejectedError, ZontCommandStateError
from .object_import import object_import_configuration
from .object_platform import ZontObjectEntityReconciler
from .objects import ZontRelayData
from .relay import (
    ZontRelayConfiguration,
    async_set_relay_state_and_confirm,
    relay_logical_state,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT relay switches."""

    @callback
    def object_entity_factories() -> dict[int, Callable[[], SwitchEntity]]:
        """Describe relay switches selected by current import options."""
        factories: dict[int, Callable[[], SwitchEntity]] = {}
        import_configuration = object_import_configuration(entry.options)
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if not import_configuration.imports(obj.object_id) or not isinstance(
                obj, ZontRelayData
            ):
                continue
            factories[obj.object_id] = partial(
                ZontRelaySwitch,
                entry,
                obj.object_id,
            )
        return factories

    reconciler = ZontObjectEntityReconciler(
        hass,
        entry,
        async_add_entities,
        object_entity_factories,
    )
    entry.async_on_unload(entry.runtime_data.object_entities.async_register(reconciler))
    entry.async_on_unload(
        entry.runtime_data.coordinator.async_add_listener(
            reconciler.async_schedule_reconcile
        )
    )
    await reconciler.async_reconcile()


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
