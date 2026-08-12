"""Shared data update coordinator for the ZONT integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HEATING_OFF_MODE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    connection_signal,
)
from .data import ZontControllerData, ZontData
from .issues import async_set_heating_off_mode_issue
from .protocol import (
    ZontClient,
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from .protocol.controller import ZontControllerInfo
from .protocol.heating_modes import (
    mode_disables_circuits,
    relevant_heating_circuit_ids,
)
from .protocol.mixer import (
    immutable_mixer_states,
)
from .protocol.objects import (
    OBJECT_TYPE_SECURITY_ZONE,
    SUPPORTED_OBJECT_TYPES,
    ZontMixerData,
    ZontMixerDirection,
    ZontObjectParseError,
    ZontSecurityZoneData,
    immutable_objects,
    parse_zont_object,
    unavailable_object,
)
from .updater import ZontDataUpdater

_LOGGER = logging.getLogger(__name__)
_FULL_REFRESH_DEBOUNCE_SECONDS = 10
_SECURITY_ZONE_PUSH_DEBOUNCE_SECONDS = 0.25


def _scan_interval_seconds(value: object) -> int:
    """Return a safe periodic control-poll interval."""
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and float(value).is_integer()
        and MIN_SCAN_INTERVAL <= value <= MAX_SCAN_INTERVAL
    ):
        return int(value)
    return DEFAULT_SCAN_INTERVAL


class ZontDataUpdateCoordinator(DataUpdateCoordinator[ZontData]):
    """Own the current ZONT data snapshot and its refresh lifecycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZontClient,
        initial_info: ZontControllerInfo | None,
        on_controller_info: Callable[[ZontControllerInfo], None],
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=_scan_interval_seconds(entry.options.get(CONF_SCAN_INTERVAL))
            ),
            always_update=False,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=_FULL_REFRESH_DEBOUNCE_SECONDS,
                immediate=True,
                background=True,
            ),
        )
        self.data = ZontData(controller=ZontControllerData(info=initial_info))
        self._entry = entry
        self._client = client
        self._updater = ZontDataUpdater(client, initial_info, on_controller_info)
        self._unsubscribe_connection: Callable[[], None] | None = None
        self._unsubscribe_messages: Callable[[], None] | None = None
        self._refresh_tasks: set[asyncio.Task[None]] = set()
        self._active_update_tasks: set[asyncio.Task[Any]] = set()
        self._pending_security_zone_ids: set[int] = set()
        self._security_zone_push_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=_SECURITY_ZONE_PUSH_DEBOUNCE_SECONDS,
            immediate=False,
            function=self._async_refresh_pending_security_zones,
        )
        self._shutting_down = False
        self._shutdown_complete = False

    @callback
    def async_start(self) -> None:
        """Subscribe to connection changes and start a non-blocking refresh."""
        if self._unsubscribe_connection is not None:
            return
        self._unsubscribe_connection = async_dispatcher_connect(
            self.hass,
            connection_signal(self._entry.entry_id),
            self._async_connection_changed,
        )
        self._unsubscribe_messages = self._client.async_add_message_listener(
            self._async_message_received
        )
        if self._client.is_connected:
            self._updater.mark_connection_stale()
            self._async_create_refresh_task("initial data refresh")

    @callback
    def async_apply_options(self) -> None:
        """Apply polling and entity-facing options without reconnecting."""
        self.update_interval = timedelta(
            seconds=_scan_interval_seconds(self._entry.options.get(CONF_SCAN_INTERVAL))
        )
        self._async_update_off_mode_issue(self.data)
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Stop scheduled updates and release coordinator subscriptions."""
        if self._shutdown_complete or self._shutting_down:
            return
        self._shutting_down = True
        if self._unsubscribe_connection is not None:
            self._unsubscribe_connection()
            self._unsubscribe_connection = None
        if self._unsubscribe_messages is not None:
            self._unsubscribe_messages()
            self._unsubscribe_messages = None
        self._security_zone_push_debouncer.async_shutdown()
        self._pending_security_zone_ids.clear()

        await super().async_shutdown()

        current_task = asyncio.current_task()
        tasks = {
            task
            for task in (*self._refresh_tasks, *self._active_update_tasks)
            if task is not current_task and not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._refresh_tasks.clear()
        self._active_update_tasks.clear()

        self._shutdown_complete = True
        self._shutting_down = False

    @callback
    def _async_create_refresh_task(self, name: str) -> None:
        """Create and track one config-entry-owned full refresh task."""
        if self._shutting_down or self._shutdown_complete:
            return
        task = self._entry.async_create_background_task(
            self.hass,
            self.async_request_refresh(),
            f"{DOMAIN} {name}",
        )
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    @callback
    def _async_connection_changed(self, connected: bool) -> None:
        """Reflect transport availability and refresh immediately on reconnect."""
        if self._shutting_down or self._shutdown_complete:
            return
        if not connected:
            self.async_set_update_error(
                UpdateFailed("The ZONT controller is disconnected")
            )
            return

        self._updater.mark_connection_stale()
        self._async_create_refresh_task("reconnect data refresh")

    async def _async_update_data(self) -> ZontData:
        """Track one active full poll so unload can finish it before client stop."""
        if self._shutting_down or self._shutdown_complete:
            raise asyncio.CancelledError
        task = asyncio.current_task()
        if task is not None:
            self._active_update_tasks.add(task)
        try:
            return await self._async_build_snapshot()
        finally:
            if task is not None:
                self._active_update_tasks.discard(task)

    async def _async_build_snapshot(self) -> ZontData:
        """Build one coherent snapshot from serialized protocol requests."""
        try:
            updated = await self._updater.async_refresh(self.data)
        except ZontConnectionError as err:
            raise UpdateFailed("Unable to update ZONT controller data") from err
        except ZontRequestTimeoutError as err:
            raise UpdateFailed("Unable to update ZONT controller data") from err
        self._async_update_off_mode_issue(updated)
        return updated

    async def async_refresh_object(self, object_id: int) -> bool:
        """Refresh one known object without running the complete coordinator poll."""
        response = await self._client.async_get_object_state(object_id)
        return self._async_apply_object_payload(response, partial=False)

    @callback
    def _async_message_received(self, payload: object) -> None:
        """Merge an unsolicited supported object state into the snapshot."""
        if self._shutting_down or self._shutdown_complete:
            return
        if not isinstance(payload, Mapping):
            return
        self._async_apply_object_payload(payload, partial=True)
        self._async_schedule_security_zone_refresh(payload)

    @callback
    def _async_schedule_security_zone_refresh(
        self,
        payload: Mapping[str, object],
    ) -> None:
        """Coalesce zone reads after a trigger-bearing object push."""
        object_id = payload.get("id")
        triggered = payload.get("trig")
        if (
            type(object_id) is not int
            or object_id < 0
            or type(triggered) is not int
            or triggered not in (0, 1)
            or payload.get("type") == OBJECT_TYPE_SECURITY_ZONE
            or isinstance(self.data.objects.get(object_id), ZontSecurityZoneData)
        ):
            return

        zone_ids = {
            obj.object_id
            for obj in self.data.objects.values()
            if isinstance(obj, ZontSecurityZoneData)
        }
        if not zone_ids:
            return
        self._pending_security_zone_ids.update(zone_ids)
        self._security_zone_push_debouncer.async_schedule_call()

    async def _async_refresh_pending_security_zones(self) -> None:
        """Refresh all zones affected by coalesced trigger-bearing pushes."""
        if self._shutting_down or self._shutdown_complete:
            self._pending_security_zone_ids.clear()
            return

        task = asyncio.current_task()
        if task is not None:
            self._active_update_tasks.add(task)
        try:
            while self._pending_security_zone_ids:
                zone_ids = tuple(sorted(self._pending_security_zone_ids))
                self._pending_security_zone_ids.difference_update(zone_ids)
                for object_id in zone_ids:
                    if not isinstance(
                        self.data.objects.get(object_id), ZontSecurityZoneData
                    ):
                        continue
                    try:
                        await self.async_refresh_object(object_id)
                    except asyncio.CancelledError:
                        raise
                    except (ZontConnectionError, ZontRequestTimeoutError):
                        _LOGGER.debug(
                            "Unable to refresh ZONT security zones after an object "
                            "trigger push because the connection was interrupted"
                        )
                        return
                    except ZontProtocolError:
                        _LOGGER.debug(
                            "Unable to refresh ZONT security zone %s after an object "
                            "trigger push",
                            object_id,
                        )
        finally:
            if task is not None:
                self._active_update_tasks.discard(task)

    @callback
    def _async_apply_object_payload(
        self,
        payload: Mapping[str, object],
        *,
        partial: bool,
    ) -> bool:
        """Merge one object payload into the current immutable snapshot."""
        object_id = payload.get("id")
        if type(object_id) is not int or object_id < 0:
            return False

        previous = self.data.objects.get(object_id)
        object_type = payload.get(
            "type",
            previous.object_type if previous is not None else None,
        )
        if object_type not in SUPPORTED_OBJECT_TYPES:
            return False

        objects = dict(self.data.objects)
        if payload.get("failed"):
            if previous is None:
                return False
            objects[object_id] = unavailable_object(previous)
        else:
            try:
                objects[object_id] = parse_zont_object(
                    payload,
                    previous,
                    partial=partial and previous is not None,
                )
            except ZontObjectParseError:
                return False

        mixer_states = self.data.mixer_states
        obj = objects[object_id]
        if (
            isinstance(obj, ZontMixerData)
            and obj.direction
            in (ZontMixerDirection.OPENING, ZontMixerDirection.CLOSING)
            and object_id in mixer_states
        ):
            mixer_states = immutable_mixer_states(
                {
                    **mixer_states,
                    object_id: mixer_states[object_id].without_end_position(),
                }
            )

        updated = ZontData(
            controller=self.data.controller,
            objects=immutable_objects(objects),
            heating_controls=self.data.heating_controls,
            heating_states=self.data.heating_states,
            heating_modes=self.data.heating_modes,
            mixer_states=mixer_states,
            relay_configurations=self.data.relay_configurations,
            relay_states=self.data.relay_states,
        )
        if updated == self.data:
            return True

        if previous is None:
            self._updater.mark_configuration_stale()

        # Keep the periodic control poll deadline: async_set_updated_data()
        # intentionally resets it, which would let frequent push messages defer
        # discovery indefinitely.
        self.data = updated
        self.async_update_listeners()
        return True

    def _async_update_off_mode_issue(self, data: ZontData) -> None:
        """Expose a proven invalid off mode as an actionable Repair issue."""
        circuit_ids = relevant_heating_circuit_ids(data.objects)
        if (
            not circuit_ids
            or not data.heating_modes
            or not circuit_ids.issubset(data.heating_states)
        ):
            return

        configured = self._entry.options.get(CONF_HEATING_OFF_MODE_ID)
        mode = data.heating_modes.get(configured) if type(configured) is int else None
        async_set_heating_off_mode_issue(
            self.hass,
            self._entry.entry_id,
            invalid=mode is None or not mode_disables_circuits(mode, circuit_ids),
        )
