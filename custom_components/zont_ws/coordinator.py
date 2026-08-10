"""Shared data update coordinator for the ZONT integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
    ZontWsClient,
)
from .const import (
    CONF_HEATING_OFF_MODE_ID,
    CONTROLLER_INFO_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    connection_signal,
)
from .controller import (
    COMMAND_SERVER_INFO,
    COMMAND_SUPPLY_VOLTAGE,
    ZontControllerInfo,
    ZontServerStatus,
    async_refresh_controller_info,
    parse_server_status_response,
    parse_supply_voltage_response,
)
from .heating_config import (
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
    immutable_heating_controls,
    immutable_heating_modes,
    immutable_heating_states,
)
from .heating_modes import mode_disables_circuits, relevant_heating_circuit_ids
from .metadata import (
    ZontHeatingMetadataRefresher,
    ZontMixerMetadataRefresher,
    ZontRelayMetadataRefresher,
)
from .mixer import (
    ZontMixerInternalState,
    immutable_mixer_states,
)
from .objects import (
    SUPPORTED_OBJECT_TYPES,
    ZontMixerData,
    ZontMixerDirection,
    ZontObject,
    ZontObjectParseError,
    immutable_objects,
    parse_zont_object,
    unavailable_object,
)
from .relay import (
    ZontRelayConfiguration,
    ZontRelayInternalState,
    immutable_relay_configurations,
    immutable_relay_states,
)

_LOGGER = logging.getLogger(__name__)

_SOURCE_SERVER_STATUS = "server_status"
_SOURCE_SUPPLY_VOLTAGE = "supply_voltage"
_CONFIG_RELOAD_MESSAGE = "CFG_RELOAD_REQ"


@dataclass(frozen=True, slots=True)
class ZontControllerData:
    """Descriptive and live data reported by the controller itself."""

    info: ZontControllerInfo | None
    server_status: ZontServerStatus | None = None
    supply_voltage: float | None = None


@dataclass(frozen=True, slots=True)
class ZontData:
    """Immutable integration data snapshot."""

    controller: ZontControllerData
    objects: Mapping[int, ZontObject] = immutable_objects()
    heating_controls: Mapping[int, ZontHeatingCircuitControlData] = (
        immutable_heating_controls()
    )
    heating_states: Mapping[int, ZontHeatingCircuitInternalState] = (
        immutable_heating_states()
    )
    heating_modes: Mapping[int, ZontHeatingModeConfiguration] = (
        immutable_heating_modes()
    )
    mixer_states: Mapping[int, ZontMixerInternalState] = immutable_mixer_states()
    relay_configurations: Mapping[int, ZontRelayConfiguration] = (
        immutable_relay_configurations()
    )
    relay_states: Mapping[int, ZontRelayInternalState] = immutable_relay_states()


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
        client: ZontWsClient,
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
        )
        self.data = ZontData(controller=ZontControllerData(info=initial_info))
        self._entry = entry
        self._client = client
        self._on_controller_info = on_controller_info
        self._disabled_sources: set[str] = set()
        self._info_refresh_enabled = initial_info is not None
        self._info_refresh_needed = initial_info is not None
        self._unsubscribe_connection: Callable[[], None] | None = None
        self._unsubscribe_messages: Callable[[], None] | None = None
        self._refresh_tasks: set[asyncio.Task[None]] = set()
        self._active_update_tasks: set[asyncio.Task[Any]] = set()
        self._object_error_types: set[int] = set()
        self._heating_metadata = ZontHeatingMetadataRefresher(client)
        self._mixer_metadata = ZontMixerMetadataRefresher(client)
        self._relay_metadata = ZontRelayMetadataRefresher(client)
        self._off_mode_warning_active = False
        self._shutting_down = False
        self._shutdown_complete = False

    @property
    def disabled_sources(self) -> tuple[str, ...]:
        """Return controller data sources disabled until the next reload."""
        return tuple(sorted(self._disabled_sources))

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
        self._async_create_refresh_task("initial data refresh")

    @callback
    def async_apply_options(self) -> None:
        """Apply polling and entity-facing options without reconnecting."""
        self.update_interval = timedelta(
            seconds=_scan_interval_seconds(self._entry.options.get(CONF_SCAN_INTERVAL))
        )
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
            self.async_refresh(),
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

        if self._info_refresh_enabled:
            self._info_refresh_needed = True
        self._heating_metadata.mark_stale()
        self._relay_metadata.mark_stale()
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
        if not self._client.is_connected:
            raise UpdateFailed("The ZONT controller is disconnected")

        previous_data = self.data
        previous = previous_data.controller
        try:
            info = await self._async_refresh_info(previous.info)
            server_status = await self._async_refresh_server_status()
            supply_voltage = await self._async_refresh_supply_voltage()
            objects = await self._async_refresh_objects(previous_data.objects)
            mixer_states = await self._mixer_metadata.async_refresh(objects)
            (
                relay_configurations,
                relay_states,
            ) = await self._relay_metadata.async_refresh(objects)
            (
                heating_controls,
                heating_states,
                heating_modes,
            ) = await self._heating_metadata.async_refresh(
                objects,
                previous_data.heating_modes,
            )
        except ZontConnectionError as err:
            raise UpdateFailed("Unable to update ZONT controller data") from err
        except ZontRequestTimeoutError as err:
            raise UpdateFailed("Unable to update ZONT controller data") from err

        if not self._client.is_connected:
            raise UpdateFailed("The ZONT controller disconnected during update")

        updated = ZontData(
            controller=ZontControllerData(
                info=info,
                server_status=server_status,
                supply_voltage=supply_voltage,
            ),
            objects=objects,
            heating_controls=heating_controls,
            heating_states=heating_states,
            heating_modes=heating_modes,
            mixer_states=mixer_states,
            relay_configurations=relay_configurations,
            relay_states=relay_states,
        )
        self._log_invalid_off_mode(updated)
        return updated

    async def _async_refresh_objects(
        self,
        previous: Mapping[int, ZontObject],
    ) -> Mapping[int, ZontObject]:
        """Discover and refresh all supported object types."""
        objects = {
            object_id: unavailable_object(obj) for object_id, obj in previous.items()
        }
        for object_type in SUPPORTED_OBJECT_TYPES:
            had_protocol_error = False
            try:
                object_ids = await self._client.async_get_object_ids(object_type)
            except asyncio.CancelledError:
                raise
            except (ZontConnectionError, ZontRequestTimeoutError):
                raise
            except ZontProtocolError:
                self._log_object_error(object_type)
                continue

            for object_id in object_ids:
                try:
                    response = await self._client.async_get_object_state(object_id)
                except asyncio.CancelledError:
                    raise
                except (ZontConnectionError, ZontRequestTimeoutError):
                    raise
                except ZontProtocolError:
                    had_protocol_error = True
                    continue

                if response.get("failed"):
                    continue

                try:
                    obj = parse_zont_object(
                        response,
                        previous.get(object_id),
                    )
                    if obj.object_type != object_type:
                        raise ZontObjectParseError(
                            "Object type does not match requested type"
                        )
                    objects[object_id] = obj
                except ZontObjectParseError:
                    had_protocol_error = True

            if had_protocol_error:
                self._log_object_error(object_type)
            else:
                self._object_error_types.discard(object_type)
        return immutable_objects(objects)

    async def async_refresh_object(self, object_id: int) -> bool:
        """Refresh one known object without running the complete coordinator poll."""
        response = await self._client.async_get_object_state(object_id)
        return self._async_apply_object_payload(response, partial=False)

    @callback
    def _async_message_received(self, payload: object) -> None:
        """Merge an unsolicited supported object state into the snapshot."""
        if self._shutting_down or self._shutdown_complete:
            return
        if payload == _CONFIG_RELOAD_MESSAGE:
            self._heating_metadata.mark_stale()
            self._relay_metadata.mark_stale()
            self._async_create_refresh_task("object configuration refresh")
            return
        if not isinstance(payload, Mapping):
            return
        self._async_apply_object_payload(payload, partial=True)

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

        # Keep the periodic control poll deadline: async_set_updated_data()
        # intentionally resets it, which would let frequent push messages defer
        # discovery indefinitely.
        self.data = updated
        self.async_update_listeners()
        return True

    def _log_invalid_off_mode(self, data: ZontData) -> None:
        """Log once while the configured mode no longer disables all circuits."""
        configured = self._entry.options.get(CONF_HEATING_OFF_MODE_ID)
        if type(configured) is not int:
            self._off_mode_warning_active = False
            return
        mode = data.heating_modes.get(configured)
        valid = mode is not None and mode_disables_circuits(
            mode,
            relevant_heating_circuit_ids(data.objects),
        )
        if valid:
            self._off_mode_warning_active = False
            return
        if self._off_mode_warning_active:
            return
        self._off_mode_warning_active = True
        _LOGGER.warning(
            "Configured ZONT off mode %s no longer disables all DHW and consumer "
            "circuits; heating on/off controls are disabled until it is reconfigured",
            configured,
        )

    def _log_object_error(self, object_type: int) -> None:
        """Log one warning per type for a consecutive protocol error series."""
        if object_type in self._object_error_types:
            return
        self._object_error_types.add(object_type)
        _LOGGER.warning(
            "Unable to read one or more ZONT objects of type %s; "
            "the integration will retry during the next update",
            object_type,
        )

    async def _async_refresh_info(
        self, previous: ZontControllerInfo | None
    ) -> ZontControllerInfo | None:
        """Refresh descriptive information once per successful connection."""
        if (
            previous is None
            or not self._info_refresh_enabled
            or not self._info_refresh_needed
        ):
            return previous

        try:
            info = await async_refresh_controller_info(
                self._client,
                previous.serial_number,
            )
        except asyncio.CancelledError:
            raise
        except ZontConnectionError:
            raise
        except (ZontProtocolError, ZontRequestTimeoutError):
            self._info_refresh_enabled = False
            self._info_refresh_needed = False
            _LOGGER.warning(
                "Unable to refresh ZONT controller information; "
                "further attempts are disabled until the integration is reloaded"
            )
            return previous

        self._info_refresh_needed = False
        if info != previous:
            self._on_controller_info(info)
        return info

    async def _async_refresh_server_status(self) -> ZontServerStatus | None:
        """Refresh cloud and active communication-channel status."""
        if _SOURCE_SERVER_STATUS in self._disabled_sources:
            return None
        try:
            response = await self._client.async_send_system_command(
                COMMAND_SERVER_INFO,
                response_timeout=CONTROLLER_INFO_TIMEOUT,
            )
            return parse_server_status_response(response)
        except asyncio.CancelledError:
            raise
        except ZontConnectionError:
            raise
        except (ValueError, ZontProtocolError, ZontRequestTimeoutError):
            self._disable_source(_SOURCE_SERVER_STATUS, COMMAND_SERVER_INFO)
            return None

    async def _async_refresh_supply_voltage(self) -> float | None:
        """Refresh controller supply voltage."""
        if _SOURCE_SUPPLY_VOLTAGE in self._disabled_sources:
            return None
        try:
            response = await self._client.async_send_system_command(
                COMMAND_SUPPLY_VOLTAGE,
                response_timeout=CONTROLLER_INFO_TIMEOUT,
            )
            return parse_supply_voltage_response(response)
        except asyncio.CancelledError:
            raise
        except ZontConnectionError:
            raise
        except (ValueError, ZontProtocolError, ZontRequestTimeoutError):
            self._disable_source(_SOURCE_SUPPLY_VOLTAGE, COMMAND_SUPPLY_VOLTAGE)
            return None

    def _disable_source(self, source: str, command: str) -> None:
        """Disable one invalid controller data source and log it once."""
        if source in self._disabled_sources:
            return
        self._disabled_sources.add(source)
        _LOGGER.warning(
            "ZONT command %s did not return supported data; "
            "further attempts are disabled until the integration is reloaded",
            command,
        )
