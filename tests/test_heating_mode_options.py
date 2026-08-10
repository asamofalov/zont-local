"""Tests for Home Assistant heating-mode option labels."""

from custom_components.zont_ws.entities.heating.mode_options import (
    heating_mode_options,
)
from custom_components.zont_ws.protocol.heating_config import (
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
)


def test_mode_options_are_ordered_unique_and_respect_reserved_names() -> None:
    """Disambiguate duplicate, literal and reserved user-facing names."""
    modes = {
        20501: ZontHeatingModeConfiguration(20501, "Комфорт", {8362: 3330}),
        20502: ZontHeatingModeConfiguration(20502, "Комфорт", {8362: 3300}),
        20503: ZontHeatingModeConfiguration(
            20503,
            "Комфорт (20501)",
            {8362: 3250},
        ),
        20504: ZontHeatingModeConfiguration(20504, "Ручной режим", {8362: 0}),
    }
    states = {
        8362: ZontHeatingCircuitInternalState(
            8362,
            4097,
            0,
            (20504, 20501, 20502, 20503),
        )
    }

    options = heating_mode_options(
        8362,
        states,
        modes,
        reserved_names=("Ручной режим",),
    )

    assert [option.label for option in options] == [
        "Ручной режим (20504)",
        "Комфорт (20501) [2]",
        "Комфорт (20502)",
        "Комфорт (20501)",
    ]
    assert [option.target for option in options] == [0, 3330, 3300, 3250]
    assert options[0].disables_circuit
    assert not options[1].disables_circuit
