"""Tests for internal ZONT mixer-state parsing."""

from __future__ import annotations

import pytest
from custom_components.zont_local.protocol.mixer import (
    ZontMixerInternalState,
    ZontMixerStateParseError,
    immutable_mixer_states,
    parse_mixer_internal_state,
)
from custom_components.zont_local.protocol.objects import ZontMixerDirection


@pytest.mark.parametrize(
    ("response", "direction", "flags"),
    [
        ("#Y9078$1,8", ZontMixerDirection.OPENING, 8),
        ("#Y9078$1,24", ZontMixerDirection.OPENING, 24),
        ("#Y9078$0,20", ZontMixerDirection.IDLE, 20),
        ("#Y9078$0,18", ZontMixerDirection.IDLE, 18),
        (" #Y9078$2,224 ", ZontMixerDirection.CLOSING, 224),
    ],
)
def test_parse_mixer_internal_state(
    response: str,
    direction: ZontMixerDirection,
    flags: int,
) -> None:
    assert parse_mixer_internal_state(response, 9078) == ZontMixerInternalState(
        object_id=9078,
        direction=direction,
        state_flags=flags,
    )


def test_mixer_internal_state_exposes_supported_flags() -> None:
    state = parse_mixer_internal_state("#Y9078$0,227")

    assert state.is_fully_open
    assert state.is_fully_closed
    assert state.has_sensor_fault
    assert state.has_output_fault
    assert state.has_set_failed
    assert state.has_fault


@pytest.mark.parametrize("flags", [32, 64, 128, 224])
def test_mixer_internal_state_aggregates_fault_flags(flags: int) -> None:
    assert ZontMixerInternalState(9078, ZontMixerDirection.IDLE, flags).has_fault


def test_mixer_internal_state_without_fault_flags_is_healthy() -> None:
    state = ZontMixerInternalState(9078, ZontMixerDirection.IDLE, 31)

    assert not state.has_fault


def test_mixer_internal_state_clears_only_end_position_flags() -> None:
    state = parse_mixer_internal_state("#Y9078$0,227")

    moving = state.without_end_position()

    assert moving.state_flags == 224
    assert not moving.is_fully_open
    assert not moving.is_fully_closed
    assert moving.has_sensor_fault
    assert moving.has_output_fault
    assert moving.has_set_failed


@pytest.mark.parametrize(
    "response",
    [
        "",
        "#Y9078$3,0",
        "#Y9078$0,-1",
        "#Y9078$0,1,2",
        "#Z9078:0,18",
        "#Ybad$0,18",
        "x" * 257,
    ],
)
def test_invalid_mixer_internal_state_is_rejected(response: str) -> None:
    with pytest.raises(ZontMixerStateParseError):
        parse_mixer_internal_state(response)


def test_mixer_internal_state_validates_expected_id() -> None:
    with pytest.raises(ZontMixerStateParseError):
        parse_mixer_internal_state("#Y9078$0,18", 9044)


def test_mixer_state_mapping_is_immutable() -> None:
    state = parse_mixer_internal_state("#Y9078$0,18")
    source = {9078: state}

    states = immutable_mixer_states(source)
    source.clear()

    assert states == {9078: state}
    with pytest.raises(TypeError):
        states[9078] = state  # type: ignore[index]
