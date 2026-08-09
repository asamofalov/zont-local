"""Shared data update coordinator for the ZONT integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta

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
    OBJECT_TYPE_HEATING_MODE,
    ZontConsumerControlMode,
    ZontHeatingCircuitConfiguration,
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
    ZontHeatingConfigParseError,
    ZontHeatingModeConfiguration,
    ZontTemperatureSensorConfiguration,
    immutable_heating_controls,
    immutable_heating_modes,
    immutable_heating_states,
    parse_heating_circuit_configuration,
    parse_heating_circuit_internal_state,
    parse_heating_mode_configuration,
    parse_temperature_sensor_configuration,
    resolve_heating_circuit_control,
)
from .heating_modes import mode_disables_circuits, relevant_heating_circuit_ids
from .objects import (
    SUPPORTED_OBJECT_TYPES,
    ZontHeatingCircuitData,
    ZontObject,
    ZontObjectParseError,
    immutable_objects,
    parse_zont_object,
    unavailable_object,
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


@dataclass(slots=True)
class ZontRuntimeData:
    """Runtime resources owned by one ZONT config entry."""

    client: ZontWsClient
    coordinator: ZontDataUpdateCoordinator


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
        self._initial_refresh_task: asyncio.Task[None] | None = None
        self._object_error_types: set[int] = set()
        self._heating_configurations: dict[int, ZontHeatingCircuitConfiguration] = {}
        self._heating_target_sensor_ids: dict[int, int | None] = {}
        self._temperature_sensor_configurations: dict[
            int, ZontTemperatureSensorConfiguration
        ] = {}
        self._heating_configuration_refresh_needed = True
        self._heating_metadata_errors: set[tuple[str, int]] = set()
        self._off_mode_warning_active = False
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
        self._heating_configuration_refresh_needed = True
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
            (
                heating_controls,
                heating_states,
                heating_modes,
            ) = await self._async_refresh_heating_metadata(objects)
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
        if payload == _CONFIG_RELOAD_MESSAGE:
            self._heating_configuration_refresh_needed = True
            self._entry.async_create_background_task(
                self.hass,
                self.async_refresh(),
                f"{DOMAIN} heating configuration refresh",
            )
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

        updated = ZontData(
            controller=self.data.controller,
            objects=immutable_objects(objects),
            heating_controls=self.data.heating_controls,
            heating_states=self.data.heating_states,
            heating_modes=self.data.heating_modes,
        )
        if updated == self.data:
            return True

        # Keep the periodic control poll deadline: async_set_updated_data()
        # intentionally resets it, which would let frequent push messages defer
        # discovery indefinitely.
        self.data = updated
        self.async_update_listeners()
        return True

    async def _async_refresh_heating_metadata(
        self,
        objects: Mapping[int, ZontObject],
    ) -> tuple[
        Mapping[int, ZontHeatingCircuitControlData],
        Mapping[int, ZontHeatingCircuitInternalState],
        Mapping[int, ZontHeatingModeConfiguration],
    ]:
        """Refresh heating modes, controls, and internal circuit states."""
        circuit_ids = {
            obj.object_id
            for obj in objects.values()
            if isinstance(obj, ZontHeatingCircuitData) and obj.subtype in (1, 3)
        }
        if not circuit_ids:
            self._heating_configuration_refresh_needed = False
            return (
                immutable_heating_controls(),
                immutable_heating_states(),
                immutable_heating_modes(),
            )

        force_configuration = self._heating_configuration_refresh_needed
        refresh_incomplete = False
        controls: dict[int, ZontHeatingCircuitControlData] = {}
        states: dict[int, ZontHeatingCircuitInternalState] = {}
        modes = self.data.heating_modes
        if force_configuration:
            modes, modes_incomplete = await self._async_refresh_heating_modes()
            refresh_incomplete |= modes_incomplete
        for object_id in sorted(circuit_ids):
            circuit = objects[object_id]
            assert isinstance(circuit, ZontHeatingCircuitData)
            configuration = self._heating_configurations.get(object_id)
            if circuit.subtype == 3 and (force_configuration or configuration is None):
                try:
                    response = await self._client.async_send_system_command(
                        f"#Z{object_id}?"
                    )
                    refreshed_configuration = parse_heating_circuit_configuration(
                        response,
                        object_id,
                    )
                    if refreshed_configuration.subtype != 3:
                        raise ZontHeatingConfigParseError(
                            "Heating-circuit subtype does not match the state"
                        )
                except asyncio.CancelledError:
                    raise
                except (ZontConnectionError, ZontRequestTimeoutError):
                    raise
                except (ZontProtocolError, ZontHeatingConfigParseError):
                    refresh_incomplete = True
                    self._log_heating_metadata_error("configuration", object_id)
                else:
                    configuration = refreshed_configuration
                    self._heating_configurations[object_id] = configuration
                    self._heating_metadata_errors.discard(("configuration", object_id))

            target_sensor_id = self._heating_target_sensor_ids.get(object_id)
            try:
                response = await self._client.async_send_system_command(
                    f"#Y{object_id}?"
                )
                internal_state = parse_heating_circuit_internal_state(
                    response,
                    object_id,
                )
            except asyncio.CancelledError:
                raise
            except (ZontConnectionError, ZontRequestTimeoutError):
                raise
            except (ZontProtocolError, ZontHeatingConfigParseError):
                self._log_heating_metadata_error("state", object_id)
            else:
                target_sensor_id = internal_state.target_sensor_id
                self._heating_target_sensor_ids[object_id] = target_sensor_id
                states[object_id] = internal_state
                self._heating_metadata_errors.discard(("state", object_id))

            if circuit.subtype != 3 or configuration is None:
                continue

            control = resolve_heating_circuit_control(
                configuration,
                target_sensor_id,
            )
            sensor_configuration = None
            if self._control_needs_sensor_configuration(configuration, control):
                sensor_configuration = self._temperature_sensor_configurations.get(
                    target_sensor_id
                )
                if target_sensor_id is not None and (
                    force_configuration or sensor_configuration is None
                ):
                    try:
                        response = await self._client.async_send_system_command(
                            f"#Z{target_sensor_id}?"
                        )
                        refreshed_sensor_configuration = (
                            parse_temperature_sensor_configuration(
                                response,
                                target_sensor_id,
                            )
                        )
                    except asyncio.CancelledError:
                        raise
                    except (ZontConnectionError, ZontRequestTimeoutError):
                        raise
                    except (ZontProtocolError, ZontHeatingConfigParseError):
                        refresh_incomplete = True
                        self._log_heating_metadata_error(
                            "temperature sensor",
                            target_sensor_id,
                        )
                    else:
                        sensor_configuration = refreshed_sensor_configuration
                        self._temperature_sensor_configurations[target_sensor_id] = (
                            sensor_configuration
                        )
                        self._heating_metadata_errors.discard(
                            ("temperature sensor", target_sensor_id)
                        )

            controls[object_id] = resolve_heating_circuit_control(
                configuration,
                target_sensor_id,
                sensor_configuration,
            )

        self._heating_configuration_refresh_needed = refresh_incomplete
        return (
            immutable_heating_controls(controls),
            immutable_heating_states(states),
            modes,
        )

    async def _async_refresh_heating_modes(
        self,
    ) -> tuple[Mapping[int, ZontHeatingModeConfiguration], bool]:
        """Discover current named heating modes and their circuit targets."""
        try:
            mode_ids = await self._client.async_get_object_ids(OBJECT_TYPE_HEATING_MODE)
        except asyncio.CancelledError:
            raise
        except (ZontConnectionError, ZontRequestTimeoutError):
            raise
        except ZontProtocolError:
            self._log_heating_metadata_error("mode discovery", 0)
            return immutable_heating_modes(), True

        modes: dict[int, ZontHeatingModeConfiguration] = {}
        refresh_incomplete = False
        for mode_id in mode_ids:
            try:
                response = await self._client.async_send_system_command(f"#Z{mode_id}?")
                modes[mode_id] = parse_heating_mode_configuration(response, mode_id)
            except asyncio.CancelledError:
                raise
            except (ZontConnectionError, ZontRequestTimeoutError):
                raise
            except (ZontProtocolError, ZontHeatingConfigParseError):
                refresh_incomplete = True
                self._log_heating_metadata_error("mode configuration", mode_id)
            else:
                self._heating_metadata_errors.discard(("mode configuration", mode_id))

        self._heating_metadata_errors.discard(("mode discovery", 0))
        return immutable_heating_modes(modes), refresh_incomplete

    @staticmethod
    def _control_needs_sensor_configuration(
        configuration: ZontHeatingCircuitConfiguration,
        control: ZontHeatingCircuitControlData,
    ) -> bool:
        """Return whether sensor thresholds can refine a supported range."""
        if not control.can_set_temperature or control.target_sensor_id is None:
            return False
        if control.control_mode in (
            ZontConsumerControlMode.AIR,
            ZontConsumerControlMode.AIR_PID,
        ):
            return True
        return (
            control.control_mode is ZontConsumerControlMode.WATER
            and not configuration.uses_weather_compensated_setpoint
            and (
                configuration.water_min_temperature is None
                or configuration.water_max_temperature is None
            )
        )

    def _log_heating_metadata_error(self, source: str, object_id: int) -> None:
        """Log one warning for a consecutive internal metadata failure."""
        key = (source, object_id)
        if key in self._heating_metadata_errors:
            return
        self._heating_metadata_errors.add(key)
        _LOGGER.warning(
            "Unable to read ZONT heating %s for object %s; "
            "the integration will retry during the next update",
            source,
            object_id,
        )

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
