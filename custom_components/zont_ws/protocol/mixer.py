"""Internal read-only state of ZONT mixers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from .objects import ZontMixerDirection

MIXER_FLAG_FULLY_OPEN = 1
MIXER_FLAG_FULLY_CLOSED = 2
MIXER_FLAG_SENSOR_FAULT = 32
MIXER_FLAG_OUTPUT_FAULT = 64
MIXER_FLAG_SET_FAILED = 128

_END_POSITION_FLAGS = MIXER_FLAG_FULLY_OPEN | MIXER_FLAG_FULLY_CLOSED
_MAX_RESPONSE_LENGTH = 256
_MIXER_STATE_PATTERN = re.compile(r"^#Y([0-9]+)\$([0-2]),([0-9]+)$")


class ZontMixerStateParseError(ValueError):
    """Raised when an internal mixer-state response is malformed."""


@dataclass(frozen=True, slots=True)
class ZontMixerInternalState:
    """Internal movement, end-position, and diagnostic flags of one mixer."""

    object_id: int
    direction: ZontMixerDirection
    state_flags: int

    @property
    def is_fully_open(self) -> bool:
        """Return whether the fully-open limit is active."""
        return bool(self.state_flags & MIXER_FLAG_FULLY_OPEN)

    @property
    def is_fully_closed(self) -> bool:
        """Return whether the fully-closed limit is active."""
        return bool(self.state_flags & MIXER_FLAG_FULLY_CLOSED)

    @property
    def has_sensor_fault(self) -> bool:
        """Return whether the mixer position sensor reports a fault."""
        return bool(self.state_flags & MIXER_FLAG_SENSOR_FAULT)

    @property
    def has_output_fault(self) -> bool:
        """Return whether the mixer output reports a fault."""
        return bool(self.state_flags & MIXER_FLAG_OUTPUT_FAULT)

    @property
    def has_set_failed(self) -> bool:
        """Return whether the controller failed to set the mixer output."""
        return bool(self.state_flags & MIXER_FLAG_SET_FAILED)

    def without_end_position(self) -> ZontMixerInternalState:
        """Clear stale limit flags when a new movement has begun."""
        return replace(self, state_flags=self.state_flags & ~_END_POSITION_FLAGS)


def immutable_mixer_states(
    states: Mapping[int, ZontMixerInternalState] | None = None,
) -> Mapping[int, ZontMixerInternalState]:
    """Return an immutable copy of mixer internal states."""
    return MappingProxyType(dict(states or {}))


def parse_mixer_internal_state(
    response: str,
    expected_object_id: int | None = None,
) -> ZontMixerInternalState:
    """Parse the documented fields of a ``#Y<id>`` mixer response."""
    if not isinstance(response, str) or len(response) > _MAX_RESPONSE_LENGTH:
        raise ZontMixerStateParseError("Mixer state response is invalid")

    match = _MIXER_STATE_PATTERN.fullmatch(response.strip())
    if match is None:
        raise ZontMixerStateParseError("Mixer state response is malformed")

    object_id = int(match.group(1))
    if expected_object_id is not None and object_id != expected_object_id:
        raise ZontMixerStateParseError("Mixer state belongs to another object")

    return ZontMixerInternalState(
        object_id=object_id,
        direction={
            0: ZontMixerDirection.IDLE,
            1: ZontMixerDirection.OPENING,
            2: ZontMixerDirection.CLOSING,
        }[int(match.group(2))],
        state_flags=int(match.group(3)),
    )
