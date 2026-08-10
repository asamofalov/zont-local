"""Live reconciliation for Home Assistant entities backed by ZONT objects."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Hashable, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

type ZontObjectEntityFactory = Callable[[], Entity]
type ZontObjectEntityFactories = Mapping[Hashable, ZontObjectEntityFactory]


class ZontObjectEntityReconciler:
    """Keep one entity platform aligned with the current import options."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
        entity_factories: Callable[[], ZontObjectEntityFactories],
    ) -> None:
        """Initialize one platform reconciler."""
        self._hass = hass
        self._entry = entry
        self._async_add_entities = async_add_entities
        self._entity_factories = entity_factories
        self._entities: dict[Hashable, Entity] = {}
        self._lock = asyncio.Lock()
        self._scheduled_task: asyncio.Task[None] | None = None
        self._stopped = False

    async def async_reconcile(self) -> None:
        """Add selected entities and remove only entities no longer selected."""
        if self._stopped:
            return
        async with self._lock:
            if self._stopped:
                return
            desired = self._entity_factories()
            for identity in tuple(self._entities):
                if identity in desired:
                    continue
                entity = self._entities.pop(identity)
                await self._async_remove_entity(entity)

            additions: list[Entity] = []
            for identity, factory in desired.items():
                if identity in self._entities:
                    continue
                entity = factory()
                self._entities[identity] = entity
                additions.append(entity)
            if additions:
                self._async_add_entities(additions)

    @callback
    def async_schedule_reconcile(self) -> None:
        """Schedule one coalesced reconciliation after coordinator discovery."""
        if self._stopped or (
            self._scheduled_task is not None and not self._scheduled_task.done()
        ):
            return
        self._scheduled_task = self._entry.async_create_background_task(
            self._hass,
            self.async_reconcile(),
            "zont_ws object entity reconciliation",
        )
        self._scheduled_task.add_done_callback(self._async_scheduled_task_done)

    async def async_shutdown(self) -> None:
        """Stop scheduled reconciliation work during config entry unload."""
        self._stopped = True
        task = self._scheduled_task
        self._scheduled_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _async_remove_entity(self, entity: Entity) -> None:
        """Remove one live entity and its stale registry entry."""
        entity_id = entity.entity_id
        if entity.hass is not None:
            await entity.async_remove(force_remove=True)
        if entity_id is not None:
            registry = er.async_get(self._hass)
            if registry.async_get(entity_id) is not None:
                registry.async_remove(entity_id)

    @callback
    def _async_scheduled_task_done(self, task: asyncio.Task[None]) -> None:
        """Forget a completed scheduled reconciliation."""
        if self._scheduled_task is task:
            self._scheduled_task = None


class ZontObjectEntityManager:
    """Coordinate live import changes across all object platforms."""

    def __init__(self) -> None:
        """Initialize an empty platform collection."""
        self._reconcilers: set[ZontObjectEntityReconciler] = set()

    @callback
    def async_register(
        self, reconciler: ZontObjectEntityReconciler
    ) -> Callable[[], None]:
        """Register one platform reconciler until config entry unload."""
        self._reconcilers.add(reconciler)

        @callback
        def async_unregister() -> None:
            self._reconcilers.discard(reconciler)

        return async_unregister

    async def async_reconcile(self) -> None:
        """Apply current import options to all loaded object platforms."""
        if self._reconcilers:
            await asyncio.gather(
                *(reconciler.async_reconcile() for reconciler in self._reconcilers)
            )

    async def async_shutdown(self) -> None:
        """Stop all platform-owned reconciliation tasks."""
        reconcilers = tuple(self._reconcilers)
        self._reconcilers.clear()
        if reconcilers:
            await asyncio.gather(
                *(reconciler.async_shutdown() for reconciler in reconcilers)
            )
