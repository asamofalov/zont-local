"""Immutable snapshots shared by ZONT entities and the coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .protocol.controller import (
    ZontControllerInfo,
    ZontEthernetStatus,
    ZontGsmStatus,
    ZontPowerSource,
    ZontServerStatus,
    ZontWifiStatus,
)
from .protocol.heating_config import (
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
    immutable_heating_controls,
    immutable_heating_modes,
    immutable_heating_states,
)
from .protocol.mixer import ZontMixerInternalState, immutable_mixer_states
from .protocol.objects import ZontObject, immutable_objects
from .protocol.relay import (
    ZontRelayConfiguration,
    ZontRelayInternalState,
    immutable_relay_configurations,
    immutable_relay_states,
)


@dataclass(frozen=True, slots=True)
class ZontControllerData:
    """Descriptive and live data reported by the controller itself."""

    info: ZontControllerInfo | None
    server_status: ZontServerStatus | None = None
    supply_voltage: float | None = None
    power_source: ZontPowerSource | None = None
    wifi_status: ZontWifiStatus | None = None
    ethernet_status: ZontEthernetStatus | None = None
    gsm_status: ZontGsmStatus | None = None


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
