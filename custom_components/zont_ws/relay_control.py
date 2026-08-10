"""Home Assistant state confirmation for ZONT relay commands."""

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
from .protocol.objects import ZontRelayData
from .protocol.relay import relay_logical_state


async def async_set_relay_state_and_confirm(
    client: ZontClient,
    coordinator: ZontDataUpdateCoordinator,
    object_id: int,
    is_on: bool,
) -> None:
    """Set a logical relay state and confirm it from a fresh object state."""
    response = await client.async_send_command(object_id, int(is_on))
    result = response.get("cmdres")
    if type(result) is not int or result != 0:
        raise ZontCommandRejectedError(result)

    try:
        refreshed = await coordinator.async_refresh_object(object_id)
    except asyncio.CancelledError:
        raise
    except (ZontConnectionError, ZontRequestTimeoutError, ZontProtocolError) as err:
        raise ZontCommandStateError(
            "The accepted relay state could not be confirmed"
        ) from err
    if not refreshed:
        raise ZontCommandStateError(
            "The accepted relay command did not return a usable state"
        )

    relay = coordinator.data.objects.get(object_id)
    configuration = coordinator.data.relay_configurations.get(object_id)
    if (
        not isinstance(relay, ZontRelayData)
        or configuration is None
        or relay_logical_state(relay, configuration) is not is_on
    ):
        raise ZontCommandStateError("The relay state was not applied")
