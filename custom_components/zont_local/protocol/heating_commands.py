"""Heating circuit protocol commands."""

from __future__ import annotations

from math import isfinite
from typing import Any

from .client import ZontClient
from .errors import ZontProtocolError

DECIKELVIN_OFFSET = 2730


class ZontCommandRejectedError(ZontProtocolError):
    """Raised when the controller rejects an object command."""

    def __init__(self, result: Any) -> None:
        """Initialize an error with the controller result."""
        self.result = result
        super().__init__("The ZONT controller rejected the command")


class ZontCommandStateError(ZontProtocolError):
    """Raised when an accepted heating command is not confirmed by state."""


def celsius_to_decikelvin(temperature: float) -> int:
    """Convert degrees Celsius to the ZONT decikelvin command value."""
    if not isfinite(temperature):
        raise ValueError("Temperature must be finite")
    return round((temperature + DECIKELVIN_OFFSET / 10) * 10)


async def async_set_heating_circuit_temperature(
    client: ZontClient,
    object_id: int,
    temperature: float,
) -> None:
    """Set a heating circuit target temperature and validate acceptance."""
    response = await client.async_send_command(
        object_id,
        celsius_to_decikelvin(temperature),
    )
    result = response.get("cmdres")
    if type(result) is not int or result != 0:
        raise ZontCommandRejectedError(result)
