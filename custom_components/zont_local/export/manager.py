"""Synchronize configured Home Assistant entities with ZONT objects."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from ..const import (
    DOMAIN,
    EXPORT_HEARTBEAT_INTERVAL,
    connection_signal,
)
from ..protocol import (
    ZontClient,
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from .model import (
    ZontExportBinding,
    command_response_id,
    export_bindings,
    export_command,
    export_issue_id,
    export_target_matches,
)
from .source import (
    ZontExportSourceError,
    ZontExportSourceUnavailable,
    resolve_export_source,
    validate_export_source,
)


class ZontExportManager:
    """Synchronize configured Home Assistant entities with ZONT."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZontClient,
    ) -> None:
        """Initialize the export manager."""
        self.hass = hass
        self._entry = entry
        self._client = client
        self._bindings = export_bindings(entry.options)
        self._resolved_sources: dict[int, str] = {}
        self._validated_targets: set[int] = set()
        self._active_targets: set[int] = set()
        self._error_targets: set[int] = set()
        self._sync_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._unsubscribe_states: Callable[[], None] | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._started = False
        self._subscriptions_started = False
        self._shutdown = False

    @property
    def configured_count(self) -> int:
        """Return the number of configured exports."""
        return len(self._bindings)

    @property
    def active_count(self) -> int:
        """Return the number of exports with a successful last write."""
        return len(self._active_targets)

    @property
    def error_count(self) -> int:
        """Return the number of structurally broken exports."""
        return len(self._error_targets)

    @callback
    def async_start(self) -> None:
        """Start subscriptions and the initial non-blocking synchronization."""
        if self._started:
            return
        self._started = True
        if not self._bindings:
            return
        self._async_start_subscriptions()
        if self._client.is_connected:
            self._create_task(
                self._async_sync_all(validate_targets=True),
                "initial export",
            )

    @callback
    def _async_start_subscriptions(self) -> None:
        """Start export subscriptions when at least one binding exists."""
        if self._subscriptions_started:
            return
        self._subscriptions_started = True
        self._async_subscribe_sources()
        self._unsubscribers.extend(
            (
                async_dispatcher_connect(
                    self.hass,
                    connection_signal(self._entry.entry_id),
                    self._async_connection_changed,
                ),
                self.hass.bus.async_listen(
                    er.EVENT_ENTITY_REGISTRY_UPDATED,
                    self._async_entity_registry_updated,
                ),
                async_track_time_interval(
                    self.hass,
                    self._async_heartbeat,
                    timedelta(seconds=EXPORT_HEARTBEAT_INTERVAL),
                    name=f"{DOMAIN} export heartbeat",
                ),
            )
        )

    @callback
    def _async_stop_subscriptions(self) -> None:
        """Stop subscriptions when exports are disabled or manager shuts down."""
        if self._unsubscribe_states is not None:
            self._unsubscribe_states()
            self._unsubscribe_states = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._resolved_sources.clear()
        self._subscriptions_started = False

    async def async_reconfigure(self, options: Mapping[str, Any]) -> None:
        """Apply changed export bindings without replacing the shared client."""
        bindings = export_bindings(options)
        if bindings == self._bindings:
            return

        async with self._sync_lock:
            previous = {binding.target_id: binding for binding in self._bindings}
            updated = {binding.target_id: binding for binding in bindings}
            changed_targets = {
                target_id
                for target_id in previous.keys() | updated.keys()
                if previous.get(target_id) != updated.get(target_id)
            }
            self._bindings = bindings
            self._validated_targets.difference_update(changed_targets)
            self._active_targets.difference_update(changed_targets)
            self._error_targets.difference_update(changed_targets)
            for target_id in changed_targets:
                for binding in (previous.get(target_id), updated.get(target_id)):
                    if binding is not None:
                        ir.async_delete_issue(
                            self.hass,
                            DOMAIN,
                            export_issue_id(binding),
                        )

        if self._started and self._bindings:
            self._async_start_subscriptions()
            self._async_subscribe_sources()
        elif not self._bindings:
            self._async_stop_subscriptions()
        if self._started and self._bindings and self._client.is_connected:
            self._create_task(
                self._async_sync_all(validate_targets=True),
                "reconfigured export",
            )

    async def async_shutdown(self) -> None:
        """Cancel work and release all subscriptions."""
        if self._shutdown:
            return
        self._shutdown = True
        self._async_stop_subscriptions()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @callback
    def _async_subscribe_sources(self) -> None:
        """Resolve current entity IDs and replace the state subscription."""
        resolved: dict[int, str] = {}
        for binding in self._bindings:
            entity_id = resolve_export_source(self.hass, binding.source)
            if entity_id is not None:
                resolved[binding.target_id] = entity_id
        if resolved == self._resolved_sources and self._unsubscribe_states is not None:
            return
        if self._unsubscribe_states is not None:
            self._unsubscribe_states()
        self._resolved_sources = resolved
        entity_ids = tuple(sorted(set(resolved.values())))
        self._unsubscribe_states = (
            async_track_state_change_event(
                self.hass,
                entity_ids,
                self._async_source_state_changed,
            )
            if entity_ids
            else None
        )

    @callback
    def _async_source_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Schedule immediate writes for a changed source."""
        entity_id = event.data["entity_id"]
        for binding in self._bindings:
            if self._resolved_sources.get(binding.target_id) == entity_id:
                self._create_task(
                    self._async_sync_binding(binding, validate_target=False),
                    f"export {binding.target_id}",
                )

    @callback
    def _async_entity_registry_updated(self, event: Event) -> None:
        """Follow source entity ID changes through their registry UUIDs."""
        previous = dict(self._resolved_sources)
        self._async_subscribe_sources()
        if previous != self._resolved_sources and self._client.is_connected:
            self._create_task(
                self._async_sync_all(validate_targets=False),
                "renamed export source",
            )

    @callback
    def _async_connection_changed(self, connected: bool) -> None:
        """Revalidate targets and synchronize after a reconnect."""
        if not connected:
            self._validated_targets.clear()
            self._active_targets.clear()
            return
        self._create_task(
            self._async_sync_all(validate_targets=True),
            "reconnected export",
        )

    async def _async_heartbeat(self, _now: Any) -> None:
        """Repeat the latest valid values to keep external sensors alive."""
        await self._async_sync_all(validate_targets=False)

    async def _async_sync_all(self, *, validate_targets: bool) -> None:
        """Synchronize all configured bindings sequentially."""
        if self._shutdown or not self._client.is_connected:
            return
        self._async_subscribe_sources()
        async with self._sync_lock:
            for binding in self._bindings:
                await self._async_sync_binding_locked(
                    binding,
                    validate_target=validate_targets,
                )

    async def _async_sync_binding(
        self,
        binding: ZontExportBinding,
        *,
        validate_target: bool,
    ) -> None:
        """Synchronize one binding while serializing export writes."""
        if self._shutdown or not self._client.is_connected:
            return
        async with self._sync_lock:
            if binding not in self._bindings:
                return
            await self._async_sync_binding_locked(
                binding,
                validate_target=validate_target,
            )

    async def _async_sync_binding_locked(
        self,
        binding: ZontExportBinding,
        *,
        validate_target: bool,
    ) -> None:
        """Validate and write one binding with the synchronization lock held."""
        if (
            validate_target or binding.target_id not in self._validated_targets
        ) and not await self._async_validate_target(binding):
            return

        entity_id = self._resolved_sources.get(binding.target_id)
        if entity_id is None or self.hass.states.get(entity_id) is None:
            self._set_issue(binding, "export_source_missing")
            return
        try:
            _, value = validate_export_source(
                self.hass,
                entity_id,
                self._entry.entry_id,
                binding.kind,
            )
        except ZontExportSourceUnavailable:
            self._active_targets.discard(binding.target_id)
            return
        except ZontExportSourceError:
            self._set_issue(binding, "export_source_invalid")
            return

        try:
            response = await self._client.async_send_command(
                binding.target_id,
                export_command(binding.kind, value),
            )
        except (ZontConnectionError, ZontRequestTimeoutError):
            self._active_targets.discard(binding.target_id)
            return
        except ZontProtocolError:
            self._set_issue(binding, "export_command_rejected")
            return

        if (
            command_response_id(response) != binding.target_id
            or type(response.get("cmdres")) is not int
            or response["cmdres"] != 0
        ):
            self._set_issue(binding, "export_command_rejected")
            return
        self._mark_healthy(binding)

    async def _async_validate_target(self, binding: ZontExportBinding) -> bool:
        """Confirm that a target ID still matches its export kind."""
        try:
            response = await self._client.async_get_object_state(binding.target_id)
        except (ZontConnectionError, ZontRequestTimeoutError):
            self._active_targets.discard(binding.target_id)
            return False
        except ZontProtocolError:
            self._set_issue(binding, "export_target_invalid")
            return False
        if not export_target_matches(
            binding.kind,
            response,
            binding.target_subtype,
        ):
            self._set_issue(binding, "export_target_invalid")
            return False
        self._validated_targets.add(binding.target_id)
        return True

    @callback
    def _mark_healthy(self, binding: ZontExportBinding) -> None:
        """Record a successful write and clear a previous structural issue."""
        self._active_targets.add(binding.target_id)
        self._error_targets.discard(binding.target_id)
        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            export_issue_id(binding),
        )

    @callback
    def _set_issue(
        self,
        binding: ZontExportBinding,
        translation_key: str,
    ) -> None:
        """Pause one broken binding and expose a single repair issue."""
        self._validated_targets.discard(binding.target_id)
        self._active_targets.discard(binding.target_id)
        self._error_targets.add(binding.target_id)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            export_issue_id(binding),
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=translation_key,
            translation_placeholders={
                "target": binding.target_name,
                "target_id": str(binding.target_id),
            },
        )

    @callback
    def _create_task(self, coroutine: Any, name: str) -> None:
        """Create and track one config-entry-owned background task."""
        if self._shutdown:
            coroutine.close()
            return
        task = self._entry.async_create_background_task(
            self.hass,
            coroutine,
            f"{DOMAIN} {name}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
