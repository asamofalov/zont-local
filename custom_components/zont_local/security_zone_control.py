"""Home Assistant state confirmation for ZONT security-zone commands."""

from __future__ import annotations

import asyncio

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
)
from .protocol.objects import ZontSecurityZoneData


async def async_set_security_zone_armed_and_confirm(
    client: ZontClient,
    coordinator: ZontDataUpdateCoordinator,
    object_id: int,
    armed: bool,
) -> None:
    """Set one security zone and confirm its actual armed state."""
    response = await client.async_send_command(object_id, int(armed))
    result = response.get("cmdres")
    if type(result) is not int or result != 0:
        raise ZontCommandRejectedError(result)

    try:
        refreshed = await coordinator.async_refresh_object(object_id)
    except asyncio.CancelledError:
        raise
    except (ZontConnectionError, ZontRequestTimeoutError, ZontProtocolError) as err:
        raise ZontCommandStateError(
            "The accepted security-zone state could not be confirmed"
        ) from err
    if not refreshed:
        raise ZontCommandStateError(
            "The accepted security-zone command did not return a usable state"
        )

    zone = coordinator.data.objects.get(object_id)
    if not isinstance(zone, ZontSecurityZoneData) or zone.armed is not armed:
        raise ZontCommandStateError("The security-zone state was not applied")


__all__ = ("async_set_security_zone_armed_and_confirm",)
