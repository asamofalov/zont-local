"""Shared data update coordinator for the ZONT integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
    ZontWsClient,
)
from .const import CONTROLLER_INFO_TIMEOUT, DOMAIN, connection_signal
from .controller import (
    COMMAND_SERVER_INFO,
    COMMAND_SUPPLY_VOLTAGE,
    ZontControllerInfo,
    ZontServerStatus,
    async_refresh_controller_info,
    parse_server_status_response,
    parse_supply_voltage_response,
)
from .objects import (
    OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
    ZontDigitalBusAdapterData,
    ZontObject,
    ZontObjectParseError,
    immutable_objects,
    parse_digital_bus_adapter,
    unavailable_object,
)

_LOGGER = logging.getLogger(__name__)

_UPDATE_INTERVAL = timedelta(minutes=1)
_SOURCE_SERVER_STATUS = "server_status"
_SOURCE_SUPPLY_VOLTAGE = "supply_voltage"


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


@dataclass(slots=True)
class ZontRuntimeData:
    """Runtime resources owned by one ZONT config entry."""

    client: ZontWsClient
    coordinator: ZontDataUpdateCoordinator


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
            update_interval=_UPDATE_INTERVAL,
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
        self._initial_refresh_task: asyncio.Task[None] | None = None
        self._object_error_logged = False
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
        self._initial_refresh_task = self._entry.async_create_background_task(
            self.hass,
            self.async_refresh(),
            f"{DOMAIN} initial data refresh",
        )

    async def async_shutdown(self) -> None:
        """Stop scheduled updates and release coordinator subscriptions."""
        if self._shutdown_complete:
            return
        if self._unsubscribe_connection is not None:
            self._unsubscribe_connection()
            self._unsubscribe_connection = None
        if self._unsubscribe_messages is not None:
            self._unsubscribe_messages()
            self._unsubscribe_messages = None

        task = self._initial_refresh_task
        self._initial_refresh_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        await super().async_shutdown()
        self._shutdown_complete = True

    @callback
    def _async_connection_changed(self, connected: bool) -> None:
        """Reflect transport availability and refresh immediately on reconnect."""
        if not connected:
            self.async_set_update_error(
                UpdateFailed("The ZONT controller is disconnected")
            )
            return

        if self._info_refresh_enabled:
            self._info_refresh_needed = True
        self._entry.async_create_background_task(
            self.hass,
            self.async_refresh(),
            f"{DOMAIN} reconnect data refresh",
        )

    async def _async_update_data(self) -> ZontData:
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
        except ZontConnectionError as err:
            raise UpdateFailed("Unable to update ZONT controller data") from err
        except ZontRequestTimeoutError as err:
            raise UpdateFailed("Unable to update ZONT controller data") from err

        if not self._client.is_connected:
            raise UpdateFailed("The ZONT controller disconnected during update")

        return ZontData(
            controller=ZontControllerData(
                info=info,
                server_status=server_status,
                supply_voltage=supply_voltage,
            ),
            objects=objects,
        )

    async def _async_refresh_objects(
        self,
        previous: Mapping[int, ZontObject],
    ) -> Mapping[int, ZontObject]:
        """Discover and refresh digital bus adapters."""
        unavailable = {
            object_id: unavailable_object(obj) for object_id, obj in previous.items()
        }
        try:
            object_ids = await self._client.async_get_object_ids(
                OBJECT_TYPE_DIGITAL_BUS_ADAPTER
            )
        except asyncio.CancelledError:
            raise
        except (ZontConnectionError, ZontRequestTimeoutError):
            raise
        except ZontProtocolError:
            self._log_object_error()
            return immutable_objects(unavailable)

        objects = unavailable
        had_protocol_error = False
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

            previous_adapter = previous.get(object_id)
            if not isinstance(previous_adapter, ZontDigitalBusAdapterData):
                previous_adapter = None
            try:
                objects[object_id] = parse_digital_bus_adapter(
                    response,
                    previous_adapter,
                )
            except ZontObjectParseError:
                had_protocol_error = True

        if had_protocol_error:
            self._log_object_error()
        else:
            self._object_error_logged = False
        return immutable_objects(objects)

    @callback
    def _async_message_received(self, payload: object) -> None:
        """Merge an unsolicited digital bus adapter state into the snapshot."""
        if not isinstance(payload, Mapping):
            return
        object_id = payload.get("id")
        if type(object_id) is not int or object_id < 0:
            return

        previous = self.data.objects.get(object_id)
        if not isinstance(previous, ZontDigitalBusAdapterData):
            previous = None
        if payload.get("type") != OBJECT_TYPE_DIGITAL_BUS_ADAPTER and previous is None:
            return

        objects = dict(self.data.objects)
        if payload.get("failed"):
            if previous is None:
                return
            objects[object_id] = unavailable_object(previous)
        else:
            try:
                objects[object_id] = parse_digital_bus_adapter(
                    payload,
                    previous,
                    partial=previous is not None,
                )
            except ZontObjectParseError:
                return

        updated = ZontData(
            controller=self.data.controller,
            objects=immutable_objects(objects),
        )
        if updated == self.data:
            return

        # Keep the periodic control poll deadline: async_set_updated_data()
        # intentionally resets it, which would let frequent push messages defer
        # discovery indefinitely.
        self.data = updated
        self.async_update_listeners()

    def _log_object_error(self) -> None:
        """Log one warning for a consecutive series of object protocol errors."""
        if self._object_error_logged:
            return
        self._object_error_logged = True
        _LOGGER.warning(
            "Unable to read one or more ZONT digital bus adapters; "
            "the integration will retry during the next update"
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
