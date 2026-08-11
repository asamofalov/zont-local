"""Build immutable controller snapshots from the ZONT protocol client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from time import monotonic

from .data import ZontControllerData, ZontData
from .protocol import (
    ZontClient,
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from .protocol.constants import CONTROLLER_INFO_TIMEOUT
from .protocol.controller import (
    COMMAND_ETHERNET_INFO,
    COMMAND_GSM_INFO,
    COMMAND_SERVER_INFO,
    COMMAND_SUPPLY_VOLTAGE,
    COMMAND_WIFI_INFO,
    ZontControllerInfo,
    ZontEthernetStatus,
    ZontGsmStatus,
    ZontPowerStatus,
    ZontServerStatus,
    ZontWifiStatus,
    async_refresh_controller_info,
    parse_ethernet_status_response,
    parse_gsm_status_response,
    parse_power_status_response,
    parse_server_status_response,
    parse_wifi_status_response,
)
from .protocol.metadata import (
    ZontHeatingMetadataRefresher,
    ZontMixerMetadataRefresher,
    ZontRelayMetadataRefresher,
)
from .protocol.objects import (
    SUPPORTED_OBJECT_TYPES,
    ZontObject,
    ZontObjectParseError,
    immutable_objects,
    parse_zont_object,
    unavailable_object,
)

_LOGGER = logging.getLogger(__name__)

_SOURCE_SERVER_STATUS = "server_status"
_SOURCE_SUPPLY_VOLTAGE = "supply_voltage"
_SOURCE_WIFI_STATUS = "wifi_status"
_SOURCE_ETHERNET_STATUS = "ethernet_status"
_SOURCE_GSM_STATUS = "gsm_status"
_CONFIGURATION_REFRESH_INTERVAL = 15 * 60


class ZontDataUpdater:
    """Read all controller sources and assemble one coherent snapshot."""

    def __init__(
        self,
        client: ZontClient,
        initial_info: ZontControllerInfo | None,
        on_controller_info: Callable[[ZontControllerInfo], None],
        monotonic_time: Callable[[], float] = monotonic,
    ) -> None:
        """Initialize protocol-backed source refreshers."""
        self._client = client
        self._on_controller_info = on_controller_info
        self._source_error_sources: set[str] = set()
        self._object_error_types: set[int] = set()
        self._info_refresh_enabled = initial_info is not None
        self._info_refresh_needed = initial_info is not None
        self._monotonic_time = monotonic_time
        self._next_configuration_refresh_at: float | None = None
        self.heating_metadata = ZontHeatingMetadataRefresher(client)
        self.mixer_metadata = ZontMixerMetadataRefresher(client)
        self.relay_metadata = ZontRelayMetadataRefresher(client)

    def mark_connection_stale(self) -> None:
        """Mark connection-scoped metadata for refresh after reconnect."""
        if self._info_refresh_enabled:
            self._info_refresh_needed = True
        self.mark_configuration_stale()

    def mark_configuration_stale(self) -> None:
        """Mark controller configuration metadata for refresh."""
        self._next_configuration_refresh_at = None
        self.heating_metadata.mark_stale()
        self.relay_metadata.mark_stale()

    async def async_refresh(self, previous: ZontData) -> ZontData:
        """Return a complete immutable snapshot using serialized requests."""
        if not self._client.is_connected:
            raise ZontConnectionError("The ZONT controller is disconnected")

        refresh_started_at = self._monotonic_time()
        configuration_refresh_due = (
            self._next_configuration_refresh_at is None
            or refresh_started_at >= self._next_configuration_refresh_at
        )
        if configuration_refresh_due:
            self.heating_metadata.mark_stale()
            self.relay_metadata.mark_stale()

        previous_controller = previous.controller
        info = await self._async_refresh_info(previous_controller.info)
        server_status = await self._async_refresh_server_status()
        power_status = await self._async_refresh_power_status()
        wifi_status = await self._async_refresh_wifi_status()
        ethernet_status = await self._async_refresh_ethernet_status()
        gsm_status = await self._async_refresh_gsm_status()
        objects = await self._async_refresh_objects(previous.objects)
        mixer_states = await self.mixer_metadata.async_refresh(objects)
        relay_configurations, relay_states = await self.relay_metadata.async_refresh(
            objects
        )
        (
            heating_controls,
            heating_states,
            heating_modes,
        ) = await self.heating_metadata.async_refresh(
            objects,
            previous.heating_modes,
        )

        if (
            configuration_refresh_due
            and not self.heating_metadata.refresh_needed
            and not self.relay_metadata.refresh_needed
        ):
            self._next_configuration_refresh_at = (
                refresh_started_at + _CONFIGURATION_REFRESH_INTERVAL
            )

        if not self._client.is_connected:
            raise ZontConnectionError("The ZONT controller disconnected during update")

        return ZontData(
            controller=ZontControllerData(
                info=info,
                server_status=server_status,
                supply_voltage=(
                    power_status.voltage if power_status is not None else None
                ),
                power_source=(
                    power_status.source if power_status is not None else None
                ),
                wifi_status=wifi_status,
                ethernet_status=ethernet_status,
                gsm_status=gsm_status,
            ),
            objects=objects,
            heating_controls=heating_controls,
            heating_states=heating_states,
            heating_modes=heating_modes,
            mixer_states=mixer_states,
            relay_configurations=relay_configurations,
            relay_states=relay_states,
        )

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
                    obj = parse_zont_object(response, previous.get(object_id))
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
        except (ZontConnectionError, ZontRequestTimeoutError):
            raise
        except ZontProtocolError:
            self._log_source_error(
                "controller_info",
                "Unable to refresh ZONT controller information; retrying later",
            )
            return previous

        self._info_refresh_needed = False
        self._source_error_sources.discard("controller_info")
        if info != previous:
            self._on_controller_info(info)
        return info

    async def _async_refresh_server_status(self) -> ZontServerStatus | None:
        """Refresh cloud and active communication-channel status."""
        return await self._async_refresh_status(
            _SOURCE_SERVER_STATUS,
            COMMAND_SERVER_INFO,
            "#S224:!",
            parse_server_status_response,
        )

    async def _async_refresh_power_status(self) -> ZontPowerStatus | None:
        """Refresh controller supply voltage and source."""
        return await self._async_refresh_status(
            _SOURCE_SUPPLY_VOLTAGE,
            COMMAND_SUPPLY_VOLTAGE,
            "#S6:!",
            parse_power_status_response,
        )

    async def _async_refresh_wifi_status(self) -> ZontWifiStatus | None:
        """Refresh optional Wi-Fi state."""
        return await self._async_refresh_optional_status(
            _SOURCE_WIFI_STATUS,
            COMMAND_WIFI_INFO,
            "#S198:!",
            parse_wifi_status_response,
        )

    async def _async_refresh_ethernet_status(self) -> ZontEthernetStatus | None:
        """Refresh optional Ethernet state."""
        return await self._async_refresh_optional_status(
            _SOURCE_ETHERNET_STATUS,
            COMMAND_ETHERNET_INFO,
            "#S205:!",
            parse_ethernet_status_response,
        )

    async def _async_refresh_gsm_status(self) -> ZontGsmStatus | None:
        """Refresh optional GSM state."""
        return await self._async_refresh_optional_status(
            _SOURCE_GSM_STATUS,
            COMMAND_GSM_INFO,
            "#S4:!",
            parse_gsm_status_response,
        )

    async def _async_refresh_optional_status[StatusT](
        self,
        source: str,
        command: str,
        unsupported_response: str,
        parser: Callable[[str], StatusT],
    ) -> StatusT | None:
        """Refresh one optional controller source without caching its absence."""
        return await self._async_refresh_status(
            source,
            command,
            unsupported_response,
            parser,
        )

    async def _async_refresh_status[StatusT](
        self,
        source: str,
        command: str,
        unavailable_response: str,
        parser: Callable[[str], StatusT],
    ) -> StatusT | None:
        """Refresh one controller source and retry every transient absence."""
        try:
            response = await self._client.async_send_system_command(
                command,
                response_timeout=CONTROLLER_INFO_TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except (ZontConnectionError, ZontRequestTimeoutError):
            raise
        except ZontProtocolError:
            self._log_source_error(
                source,
                f"ZONT command {command} returned an invalid protocol response; "
                "retrying during the next update",
            )
            return None

        if response == unavailable_response:
            self._source_error_sources.discard(source)
            return None
        try:
            status = parser(response)
        except ValueError:
            self._log_source_error(
                source,
                f"ZONT command {command} returned unrecognized data; "
                "retrying during the next update",
            )
            return None

        self._source_error_sources.discard(source)
        return status

    def _log_source_error(self, source: str, message: str) -> None:
        """Log one warning for a consecutive invalid-source response series."""
        if source in self._source_error_sources:
            return
        self._source_error_sources.add(source)
        _LOGGER.warning(message)

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
