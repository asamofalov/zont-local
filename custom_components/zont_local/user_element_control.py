"""Home Assistant command handling for ZONT user elements."""

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
from .protocol.objects import (
    USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON,
    ZontUserElementData,
)


async def async_press_user_element(client: ZontClient, object_id: int) -> None:
    """Press one stateless ZONT user element."""
    await _async_send_user_element_command(client, object_id, 1)


async def async_set_user_element_state_and_confirm(
    client: ZontClient,
    coordinator: ZontDataUpdateCoordinator,
    object_id: int,
    is_on: bool,
) -> None:
    """Set one stateful user element and confirm its observed state."""
    await _async_send_user_element_command(client, object_id, int(is_on))

    try:
        refreshed = await coordinator.async_refresh_object(object_id)
    except asyncio.CancelledError:
        raise
    except (ZontConnectionError, ZontRequestTimeoutError, ZontProtocolError) as err:
        raise ZontCommandStateError(
            "The accepted user-element state could not be confirmed"
        ) from err
    if not refreshed:
        raise ZontCommandStateError(
            "The accepted user-element command did not return a usable state"
        )

    element = coordinator.data.objects.get(object_id)
    if (
        not isinstance(element, ZontUserElementData)
        or element.subtype != USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON
        or type(element.raw_state) is not int
        or bool(element.raw_state) is not is_on
    ):
        raise ZontCommandStateError("The user-element state was not applied")


async def _async_send_user_element_command(
    client: ZontClient,
    object_id: int,
    command: int,
) -> None:
    """Send one user-element command and validate its acknowledgement."""
    response = await client.async_send_command(object_id, command)
    result = response.get("cmdres")
    if type(result) is not int or result != 0:
        raise ZontCommandRejectedError(result)


__all__ = (
    "async_press_user_element",
    "async_set_user_element_state_and_confirm",
)
