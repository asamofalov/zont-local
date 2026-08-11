"""Configuration, diagnostics, and commands for ZONT relays."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .objects import OBJECT_TYPE_RELAY, ZontRelayData

RELAY_INVERSE_MODE_FLAG_DUPLICATED = 1
RELAY_INTERNAL_ACTIVE_FLAG = 1
RELAY_INTERNAL_FAILED_FLAG = 2
RELAY_INTERNAL_TEST_MODE_FLAG = 4
RELAY_INTERNAL_TEST_PENDING_FLAG = 8
RELAY_INVERSE_MODE_FLAG = 8

_RELAY_INVERSE_FLAGS = RELAY_INVERSE_MODE_FLAG_DUPLICATED | RELAY_INVERSE_MODE_FLAG
_MAX_RESPONSE_LENGTH = 256
_RELAY_STATE_PATTERN = re.compile(r"^#Y([0-9]+)\$([0-9]+)$")
_RELAY_CONFIGURATION_PREFIX_PATTERN = re.compile(r"^#Z([0-9]+):([0-9]+),")


class ZontRelayParseError(ValueError):
    """Raised when a relay internal response is malformed."""


@dataclass(frozen=True, slots=True)
class ZontRelayConfiguration:
    """Configuration required to map a relay to its logical state."""

    object_id: int
    setting_register: int

    @property
    def is_inverse(self) -> bool:
        """Return whether either known inverse-mode flag is enabled."""
        return bool(self.setting_register & _RELAY_INVERSE_FLAGS)


@dataclass(frozen=True, slots=True)
class ZontRelayInternalState:
    """Internal runtime flags of one relay."""

    object_id: int
    state_flags: int

    @property
    def is_active(self) -> bool:
        """Return the controller's internal active flag."""
        return bool(self.state_flags & RELAY_INTERNAL_ACTIVE_FLAG)

    @property
    def has_failed(self) -> bool:
        """Return whether the relay reports a failure."""
        return bool(self.state_flags & RELAY_INTERNAL_FAILED_FLAG)

    @property
    def is_test_mode(self) -> bool:
        """Return whether relay test mode is active."""
        return bool(self.state_flags & RELAY_INTERNAL_TEST_MODE_FLAG)

    @property
    def is_test_pending(self) -> bool:
        """Return whether a relay test operation is pending."""
        return bool(self.state_flags & RELAY_INTERNAL_TEST_PENDING_FLAG)


def immutable_relay_configurations(
    configurations: Mapping[int, ZontRelayConfiguration] | None = None,
) -> Mapping[int, ZontRelayConfiguration]:
    """Return an immutable copy of relay configurations."""
    return MappingProxyType(dict(configurations or {}))


def immutable_relay_states(
    states: Mapping[int, ZontRelayInternalState] | None = None,
) -> Mapping[int, ZontRelayInternalState]:
    """Return an immutable copy of relay internal states."""
    return MappingProxyType(dict(states or {}))


def parse_relay_configuration(
    response: str,
    expected_object_id: int | None = None,
) -> ZontRelayConfiguration:
    """Parse the fields needed from a ``#Z<id>`` relay response."""
    value = _validated_response(response, "Relay configuration")
    match = _RELAY_CONFIGURATION_PREFIX_PATTERN.match(value)
    if match is None:
        raise ZontRelayParseError("Relay configuration response is malformed")

    object_id = int(match.group(1))
    object_type = int(match.group(2))
    _validate_identity(object_id, object_type, expected_object_id)

    fields = value[match.end() :].rsplit(",", 2)
    if len(fields) != 3 or not fields[0].strip():
        raise ZontRelayParseError("Relay configuration fields are missing")
    try:
        physical_output = int(fields[1])
        setting_register = int(fields[2])
    except ValueError as err:
        raise ZontRelayParseError("Relay configuration integers are invalid") from err
    if physical_output < 0 or setting_register < 0:
        raise ZontRelayParseError("Relay configuration integers must be non-negative")

    return ZontRelayConfiguration(
        object_id=object_id,
        setting_register=setting_register,
    )


def parse_relay_internal_state(
    response: str,
    expected_object_id: int | None = None,
) -> ZontRelayInternalState:
    """Parse a ``#Y<id>`` relay response."""
    value = _validated_response(response, "Relay state")
    match = _RELAY_STATE_PATTERN.fullmatch(value)
    if match is None:
        raise ZontRelayParseError("Relay state response is malformed")

    object_id = int(match.group(1))
    if expected_object_id is not None and object_id != expected_object_id:
        raise ZontRelayParseError("Relay state belongs to another object")
    return ZontRelayInternalState(
        object_id=object_id,
        state_flags=int(match.group(2)),
    )


def relay_logical_state(
    relay: ZontRelayData,
    configuration: ZontRelayConfiguration,
) -> bool | None:
    """Return the logical relay state after applying output inversion."""
    if relay.output_active is None:
        return None
    return relay.output_active != configuration.is_inverse


def _validated_response(response: str, source: str) -> str:
    """Return stripped bounded response text."""
    if not isinstance(response, str) or len(response) > _MAX_RESPONSE_LENGTH:
        raise ZontRelayParseError(f"{source} response is invalid")
    value = response.strip()
    if "\n" in value or "\r" in value:
        raise ZontRelayParseError(f"{source} response is invalid")
    return value


def _validate_identity(
    object_id: int,
    object_type: int,
    expected_object_id: int | None,
) -> None:
    """Validate relay response identity fields."""
    if object_type != OBJECT_TYPE_RELAY:
        raise ZontRelayParseError("Configuration does not describe a relay")
    if expected_object_id is not None and object_id != expected_object_id:
        raise ZontRelayParseError("Relay configuration belongs to another object")
