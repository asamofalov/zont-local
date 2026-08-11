"""Home Assistant labels for applicable ZONT heating modes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from ...protocol.heating_config import (
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
)
from ...protocol.heating_modes import applicable_heating_modes


@dataclass(frozen=True, slots=True)
class ZontHeatingModeOption:
    """One unambiguous Home Assistant option backed by a ZONT mode."""

    label: str
    mode: ZontHeatingModeConfiguration
    target: int

    @property
    def disables_circuit(self) -> bool:
        """Return whether the mode has a zero target for this circuit."""
        return self.target == 0


def heating_mode_options(
    circuit_id: int,
    states: Mapping[int, ZontHeatingCircuitInternalState],
    modes: Mapping[int, ZontHeatingModeConfiguration],
    *,
    reserved_names: Collection[str] = (),
) -> tuple[ZontHeatingModeOption, ...]:
    """Return applicable modes with unique labels in controller order."""
    applicable = applicable_heating_modes(circuit_id, states, modes)
    name_counts = Counter(mode.name for mode in applicable)
    reserved = set(reserved_names)
    reserved.update(
        mode.name
        for mode in applicable
        if name_counts[mode.name] == 1 and mode.name not in reserved_names
    )
    used: set[str] = set()
    options: list[ZontHeatingModeOption] = []
    for mode in applicable:
        if name_counts[mode.name] == 1 and mode.name not in reserved_names:
            label = mode.name
        else:
            base_label = f"{mode.name} ({mode.object_id})"
            label = base_label
            discriminator = 2
            while label in used or label in reserved:
                label = f"{base_label} [{discriminator}]"
                discriminator += 1
        used.add(label)
        options.append(
            ZontHeatingModeOption(
                label=label,
                mode=mode,
                target=mode.circuit_targets[circuit_id],
            )
        )
    return tuple(options)
