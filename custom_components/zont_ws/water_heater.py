"""Water-heater platform for ZONT hot water circuits."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from homeassistant.components.water_heater import WaterHeaterEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entities.heating.water_heater import ZontDhwWaterHeater
from .object_import import object_import_configuration
from .object_platform import ZontObjectEntityReconciler
from .protocol.heating_config import DHW_CIRCUIT_SUBTYPE
from .protocol.objects import ZontHeatingCircuitData
from .runtime import ZontRuntimeData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT hot water circuits."""

    @callback
    def object_entity_factories() -> dict[int, Callable[[], WaterHeaterEntity]]:
        """Describe water heaters selected by current import options."""
        factories: dict[int, Callable[[], WaterHeaterEntity]] = {}
        import_configuration = object_import_configuration(entry.options)
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if (
                not import_configuration.imports(obj.object_id)
                or not isinstance(obj, ZontHeatingCircuitData)
                or obj.subtype != DHW_CIRCUIT_SUBTYPE
            ):
                continue
            factories[obj.object_id] = partial(
                ZontDhwWaterHeater,
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
