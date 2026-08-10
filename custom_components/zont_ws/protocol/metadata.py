"""Specialized refreshers for ZONT object metadata."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from .client import ZontClient
from .errors import (
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
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
from .mixer import (
    ZontMixerInternalState,
    ZontMixerStateParseError,
    immutable_mixer_states,
    parse_mixer_internal_state,
)
from .objects import (
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontMixerDirection,
    ZontObject,
    ZontRelayData,
)
from .relay import (
    ZontRelayConfiguration,
    ZontRelayInternalState,
    ZontRelayParseError,
    immutable_relay_configurations,
    immutable_relay_states,
    parse_relay_configuration,
    parse_relay_internal_state,
)

_LOGGER = logging.getLogger(__name__)


class ZontMixerMetadataRefresher:
    """Refresh mixer diagnostic and end-position flags."""

    def __init__(self, client: ZontClient) -> None:
        self._client = client
        self._mixer_state_errors: set[int] = set()

    async def async_refresh(
        self,
        objects: Mapping[int, ZontObject],
    ) -> Mapping[int, ZontMixerInternalState]:
        """Refresh end-position and diagnostic flags for all mixers."""
        states: dict[int, ZontMixerInternalState] = {}
        mixer_ids = sorted(
            obj.object_id for obj in objects.values() if isinstance(obj, ZontMixerData)
        )
        for object_id in mixer_ids:
            try:
                response = await self._client.async_send_system_command(
                    f"#Y{object_id}?"
                )
                state = parse_mixer_internal_state(response, object_id)
                mixer = objects[object_id]
                assert isinstance(mixer, ZontMixerData)
                if mixer.direction in (
                    ZontMixerDirection.OPENING,
                    ZontMixerDirection.CLOSING,
                ):
                    state = state.without_end_position()
                states[object_id] = state
            except asyncio.CancelledError:
                raise
            except (ZontConnectionError, ZontRequestTimeoutError):
                raise
            except (ZontProtocolError, ZontMixerStateParseError):
                self._log_mixer_state_error(object_id)
            else:
                self._mixer_state_errors.discard(object_id)
        return immutable_mixer_states(states)

    def _log_mixer_state_error(self, object_id: int) -> None:
        """Log one warning for a consecutive mixer-state failure."""
        if object_id in self._mixer_state_errors:
            return
        self._mixer_state_errors.add(object_id)
        _LOGGER.warning(
            "Unable to read ZONT mixer state for object %s; "
            "the integration will retry during the next update",
            object_id,
        )


class ZontRelayMetadataRefresher:
    """Cache relay configuration and refresh its live diagnostic flags."""

    def __init__(self, client: ZontClient) -> None:
        self._client = client
        self._relay_configurations: dict[int, ZontRelayConfiguration] = {}
        self._relay_configuration_refresh_needed = True
        self._relay_metadata_errors: set[tuple[str, int]] = set()

    @property
    def refresh_needed(self) -> bool:
        """Return whether relay configuration must be refreshed."""
        return self._relay_configuration_refresh_needed

    def mark_stale(self) -> None:
        """Refresh configuration during the next update."""
        self._relay_configuration_refresh_needed = True

    async def async_refresh(
        self,
        objects: Mapping[int, ZontObject],
    ) -> tuple[
        Mapping[int, ZontRelayConfiguration],
        Mapping[int, ZontRelayInternalState],
    ]:
        """Refresh cached configurations and live diagnostic flags for relays."""
        relay_ids = sorted(
            obj.object_id for obj in objects.values() if isinstance(obj, ZontRelayData)
        )
        if not relay_ids:
            self._relay_configurations.clear()
            self._relay_configuration_refresh_needed = False
            return immutable_relay_configurations(), immutable_relay_states()

        force_configuration = self._relay_configuration_refresh_needed
        refresh_incomplete = False
        configurations = (
            {}
            if force_configuration
            else {
                object_id: configuration
                for object_id, configuration in self._relay_configurations.items()
                if object_id in relay_ids
            }
        )
        states: dict[int, ZontRelayInternalState] = {}
        for object_id in relay_ids:
            if force_configuration or object_id not in configurations:
                try:
                    response = await self._client.async_send_system_command(
                        f"#Z{object_id}?"
                    )
                    configurations[object_id] = parse_relay_configuration(
                        response,
                        object_id,
                    )
                except asyncio.CancelledError:
                    raise
                except (ZontConnectionError, ZontRequestTimeoutError):
                    raise
                except (ZontProtocolError, ZontRelayParseError):
                    refresh_incomplete = True
                    configurations.pop(object_id, None)
                    self._log_relay_metadata_error("configuration", object_id)
                else:
                    self._relay_metadata_errors.discard(("configuration", object_id))

            try:
                response = await self._client.async_send_system_command(
                    f"#Y{object_id}?"
                )
                states[object_id] = parse_relay_internal_state(response, object_id)
            except asyncio.CancelledError:
                raise
            except (ZontConnectionError, ZontRequestTimeoutError):
                raise
            except (ZontProtocolError, ZontRelayParseError):
                self._log_relay_metadata_error("state", object_id)
            else:
                self._relay_metadata_errors.discard(("state", object_id))

        self._relay_configurations = configurations
        self._relay_configuration_refresh_needed = refresh_incomplete
        return (
            immutable_relay_configurations(configurations),
            immutable_relay_states(states),
        )

    def _log_relay_metadata_error(self, source: str, object_id: int) -> None:
        """Log one warning for a consecutive relay metadata failure."""
        key = (source, object_id)
        if key in self._relay_metadata_errors:
            return
        self._relay_metadata_errors.add(key)
        _LOGGER.warning(
            "Unable to read ZONT relay %s for object %s; "
            "the integration will retry during the next update",
            source,
            object_id,
        )


class ZontHeatingMetadataRefresher:
    """Cache heating configuration and refresh controls and internal states."""

    def __init__(self, client: ZontClient) -> None:
        self._client = client
        self._heating_configurations: dict[int, ZontHeatingCircuitConfiguration] = {}
        self._heating_target_sensor_ids: dict[int, int | None] = {}
        self._temperature_sensor_configurations: dict[
            int, ZontTemperatureSensorConfiguration
        ] = {}
        self._heating_configuration_refresh_needed = True
        self._heating_metadata_errors: set[tuple[str, int]] = set()

    @property
    def refresh_needed(self) -> bool:
        """Return whether heating configuration must be refreshed."""
        return self._heating_configuration_refresh_needed

    def mark_stale(self) -> None:
        """Refresh configuration during the next update."""
        self._heating_configuration_refresh_needed = True

    async def async_refresh(
        self,
        objects: Mapping[int, ZontObject],
        previous_modes: Mapping[int, ZontHeatingModeConfiguration],
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
        modes = previous_modes
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
