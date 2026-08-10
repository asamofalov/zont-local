"""Discovery and validation of named ZONT heating modes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from aiohttp import ClientSession

from .errors import ZontProtocolError, ZontRequestTimeoutError
from .heating_config import (
    CONSUMER_CIRCUIT_SUBTYPE,
    DHW_CIRCUIT_SUBTYPE,
    OBJECT_TYPE_HEATING_MODE,
    ZontHeatingCircuitInternalState,
    ZontHeatingConfigParseError,
    ZontHeatingModeConfiguration,
    parse_heating_circuit_internal_state,
    parse_heating_mode_configuration,
)
from .objects import (
    OBJECT_TYPE_HEATING_CIRCUIT,
    ZontHeatingCircuitData,
    ZontObject,
    ZontObjectParseError,
    parse_zont_object,
)
from .session import (
    ZontTemporaryRequestSession,
    async_open_temporary_request_session,
)
from .types import ZontCredentials

_DISCOVERY_TIMEOUT = 15.0
_RELEVANT_CIRCUIT_SUBTYPES = frozenset({DHW_CIRCUIT_SUBTYPE, CONSUMER_CIRCUIT_SUBTYPE})


@dataclass(frozen=True, slots=True)
class ZontHeatingModeDiscovery:
    """Heating data collected by a short-lived config-flow connection."""

    circuits: Mapping[int, ZontHeatingCircuitData]
    states: Mapping[int, ZontHeatingCircuitInternalState]
    modes: Mapping[int, ZontHeatingModeConfiguration]

    @property
    def eligible_off_modes(self) -> tuple[ZontHeatingModeConfiguration, ...]:
        """Return modes proven to disable every relevant circuit."""
        return eligible_off_modes(
            self.circuits,
            self.states,
            self.modes,
        )


def relevant_heating_circuit_ids(
    objects: Mapping[int, ZontObject],
) -> frozenset[int]:
    """Return IDs of DHW and consumer circuits covered by an off mode."""
    return frozenset(
        obj.object_id
        for obj in objects.values()
        if isinstance(obj, ZontHeatingCircuitData)
        and obj.subtype in _RELEVANT_CIRCUIT_SUBTYPES
    )


def eligible_off_modes(
    objects: Mapping[int, ZontObject],
    states: Mapping[int, ZontHeatingCircuitInternalState],
    modes: Mapping[int, ZontHeatingModeConfiguration],
) -> tuple[ZontHeatingModeConfiguration, ...]:
    """Return modes proven to disable every relevant heating circuit."""
    circuit_ids = relevant_heating_circuit_ids(objects)
    if not circuit_ids:
        return ()
    return tuple(
        mode
        for mode in sorted(modes.values(), key=lambda item: item.object_id)
        if mode_disables_circuits(mode, circuit_ids)
        and all(
            mode_is_applicable_to_circuit(
                mode.object_id,
                circuit_id,
                states,
            )
            for circuit_id in circuit_ids
        )
    )


def validated_off_mode_id(
    configured_mode_id: object,
    circuit_id: int,
    objects: Mapping[int, ZontObject],
    states: Mapping[int, ZontHeatingCircuitInternalState],
    modes: Mapping[int, ZontHeatingModeConfiguration],
) -> int | None:
    """Return the configured off mode when it remains safe for a circuit."""
    if type(configured_mode_id) is not int:
        return None
    mode = modes.get(configured_mode_id)
    if mode is None or not mode_disables_circuits(
        mode,
        relevant_heating_circuit_ids(objects),
    ):
        return None
    if not mode_is_applicable_to_circuit(configured_mode_id, circuit_id, states):
        return None
    return configured_mode_id


def mode_disables_circuits(
    mode: ZontHeatingModeConfiguration,
    circuit_ids: frozenset[int],
) -> bool:
    """Return whether a mode explicitly disables all provided circuits."""
    return bool(circuit_ids) and all(
        mode.disables_circuit(circuit_id) for circuit_id in circuit_ids
    )


def mode_is_applicable_to_circuit(
    mode_id: int,
    circuit_id: int,
    states: Mapping[int, ZontHeatingCircuitInternalState],
) -> bool:
    """Return whether a circuit reports a mode as applicable."""
    state = states.get(circuit_id)
    return state is not None and mode_id in state.applicable_mode_ids


async def async_discover_heating_modes(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
) -> ZontHeatingModeDiscovery:
    """Discover circuits and modes using one bounded temporary connection."""
    async with async_open_temporary_request_session(
        session, url, credentials
    ) as requests:
        return await async_discover_heating_modes_from_requests(requests)


async def async_discover_heating_modes_from_requests(
    requests: ZontTemporaryRequestSession,
) -> ZontHeatingModeDiscovery:
    """Discover heating modes through an already authenticated connection."""
    try:
        async with asyncio.timeout(_DISCOVERY_TIMEOUT):
            circuit_ids = await requests.async_get_object_ids(
                OBJECT_TYPE_HEATING_CIRCUIT
            )
            circuits: dict[int, ZontHeatingCircuitData] = {}
            states: dict[int, ZontHeatingCircuitInternalState] = {}
            for circuit_id in circuit_ids:
                payload = await requests.async_get_object_state(circuit_id)
                if payload.get("failed"):
                    continue
                try:
                    obj = parse_zont_object(payload)
                except ZontObjectParseError as err:
                    raise ZontProtocolError(
                        f"Heating circuit {circuit_id} state is invalid"
                    ) from err
                if not isinstance(obj, ZontHeatingCircuitData):
                    raise ZontProtocolError(
                        f"Object {circuit_id} is not a heating circuit"
                    )
                if obj.subtype not in _RELEVANT_CIRCUIT_SUBTYPES:
                    continue
                circuits[circuit_id] = obj
                response = await requests.async_send_system_command(f"#Y{circuit_id}?")
                states[circuit_id] = parse_heating_circuit_internal_state(
                    response, circuit_id
                )

            mode_ids = await requests.async_get_object_ids(OBJECT_TYPE_HEATING_MODE)
            modes: dict[int, ZontHeatingModeConfiguration] = {}
            for mode_id in mode_ids:
                response = await requests.async_send_system_command(f"#Z{mode_id}?")
                modes[mode_id] = parse_heating_mode_configuration(response, mode_id)
    except (TimeoutError, ZontRequestTimeoutError) as err:
        raise ZontProtocolError("Heating-mode discovery timed out") from err
    except ZontHeatingConfigParseError as err:
        raise ZontProtocolError("Heating-mode configuration is invalid") from err

    return ZontHeatingModeDiscovery(
        circuits=MappingProxyType(circuits),
        states=MappingProxyType(states),
        modes=MappingProxyType(modes),
    )
