"""Switch platform for ZONT relays."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entities.relay import ZontRelaySwitch
from .entities.user_element import ZontUserElementSwitch
from .object_import import object_import_configuration
from .object_platform import ZontObjectEntityReconciler
from .protocol.objects import (
    USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON,
    ZontRelayData,
    ZontUserElementData,
)
from .runtime import ZontRuntimeData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT relay switches."""

    @callback
    def object_entity_factories() -> dict[int, Callable[[], SwitchEntity]]:
        """Describe selected relay and user-element switches."""
        factories: dict[int, Callable[[], SwitchEntity]] = {}
        import_configuration = object_import_configuration(entry.options)
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if not import_configuration.imports(obj.object_id):
                continue
            if isinstance(obj, ZontRelayData):
                factories[obj.object_id] = partial(
                    ZontRelaySwitch,
                    entry,
                    obj.object_id,
                )
            elif (
                isinstance(obj, ZontUserElementData)
                and obj.subtype == USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON
            ):
                factories[obj.object_id] = partial(
                    ZontUserElementSwitch,
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
