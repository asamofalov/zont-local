"""Home Assistant state confirmation for ZONT heating commands."""

from __future__ import annotations

import asyncio
import logging

from .coordinator import ZontDataUpdateCoordinator
from .protocol import (
    ZontClient,
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
)
from .protocol.heating_commands import (
    ZontCommandRejectedError,
    ZontCommandStateError,
    async_set_heating_circuit_temperature,
    celsius_to_decikelvin,
)
from .protocol.objects import ZontHeatingCircuitData, ZontHeatingCircuitMode

_LOGGER = logging.getLogger(__name__)


async def async_set_heating_circuit_temperature_and_refresh(
    client: ZontClient,
    coordinator: ZontDataUpdateCoordinator,
    object_id: int,
    temperature: float,
) -> None:
    """Set a target and read the controller state without optimistic updates."""
    await async_set_heating_circuit_temperature(client, object_id, temperature)
    try:
        refreshed = await coordinator.async_refresh_object(object_id)
    except asyncio.CancelledError:
        raise
    except (ZontConnectionError, ZontRequestTimeoutError, ZontProtocolError):
        _LOGGER.debug(
            "The ZONT controller accepted the target temperature for object %s "
            "but its state could not be refreshed",
            object_id,
        )
    else:
        if not refreshed:
            _LOGGER.debug(
                "The ZONT controller accepted the target temperature for object %s "
                "but did not return a usable state",
                object_id,
            )


async def async_set_heating_circuit_temperature_and_confirm_active(
    client: ZontClient,
    coordinator: ZontDataUpdateCoordinator,
    object_id: int,
    temperature: float,
) -> None:
    """Set a target and require the circuit to confirm an active state."""
    await async_set_heating_circuit_temperature(client, object_id, temperature)
    try:
        refreshed = await coordinator.async_refresh_object(object_id)
    except asyncio.CancelledError:
        raise
    except (ZontConnectionError, ZontRequestTimeoutError, ZontProtocolError) as err:
        raise ZontCommandStateError(
            "The accepted heating target could not be confirmed"
        ) from err
    if not refreshed:
        raise ZontCommandStateError(
            "The accepted heating target did not return a usable state"
        )

    obj = coordinator.data.objects.get(object_id)
    if (
        not isinstance(obj, ZontHeatingCircuitData)
        or obj.mode is not ZontHeatingCircuitMode.HEAT
        or obj.target_temperature is None
        or celsius_to_decikelvin(obj.target_temperature)
        != celsius_to_decikelvin(temperature)
    ):
        raise ZontCommandStateError("The heating target was not applied")


async def async_apply_heating_mode_and_refresh(
    client: ZontClient,
    coordinator: ZontDataUpdateCoordinator,
    object_id: int,
    mode_id: int,
    *,
    expect_off: bool,
) -> None:
    """Apply one named mode to one circuit and confirm its actual state."""
    response = await client.async_send_command(object_id, str(mode_id))
    result = response.get("cmdres")
    if type(result) is not int or result != 0:
        raise ZontCommandRejectedError(result)

    try:
        refreshed = await coordinator.async_refresh_object(object_id)
    except asyncio.CancelledError:
        raise
    except (ZontConnectionError, ZontRequestTimeoutError, ZontProtocolError) as err:
        raise ZontCommandStateError(
            "The accepted heating mode could not be confirmed"
        ) from err
    if not refreshed:
        raise ZontCommandStateError(
            "The accepted heating mode did not return a usable state"
        )

    obj = coordinator.data.objects.get(object_id)
    if not isinstance(obj, ZontHeatingCircuitData) or obj.mode_id != mode_id:
        raise ZontCommandStateError("The heating mode was not applied")
    if (obj.mode is ZontHeatingCircuitMode.OFF) != expect_off:
        raise ZontCommandStateError("The heating mode state is unexpected")


__all__ = (
    "ZontCommandRejectedError",
    "ZontCommandStateError",
    "async_apply_heating_mode_and_refresh",
    "async_set_heating_circuit_temperature_and_confirm_active",
    "async_set_heating_circuit_temperature_and_refresh",
)
