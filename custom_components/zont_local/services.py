"""Service actions for the ZONT Local integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN, SERVICE_SEND_BULK, SERVICE_SEND_COMMAND
from .protocol import (
    ZontClient,
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from .runtime import ZontRuntimeData

_LOGGER = logging.getLogger(__name__)

ATTR_COMMANDS = "commands"
ATTR_COMMAND_ID = "id"
ATTR_COMMAND = "cmd"

COMMAND_ID_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=0, max=999999))
COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_COMMAND_ID): COMMAND_ID_SCHEMA,
        vol.Required(ATTR_COMMAND): vol.All(vol.Coerce(str), vol.Length(min=1)),
    },
    extra=vol.PREVENT_EXTRA,
)
SEND_COMMAND_SCHEMA = COMMAND_SCHEMA
SEND_BULK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_COMMANDS): vol.All(
            [COMMAND_SCHEMA],
            vol.Length(min=1),
        )
    },
    extra=vol.PREVENT_EXTRA,
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register ZONT service actions once."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        return

    async def async_handle_send_command(
        call: ServiceCall,
    ) -> ServiceResponse | None:
        client = _get_loaded_client(hass)
        command_id = call.data[ATTR_COMMAND_ID]
        command = call.data[ATTR_COMMAND]
        _LOGGER.debug("Sending ZONT command id=%s", command_id)
        response = await _async_send(client, command_id, command)
        result = response.get("cmdres")
        _LOGGER.debug(
            "ZONT command completed id=%s result=%s",
            command_id,
            result if type(result) is int else "invalid",
        )
        if call.return_response:
            return {"response": response}
        return None

    async def async_handle_send_bulk(call: ServiceCall) -> ServiceResponse | None:
        client = _get_loaded_client(hass)
        responses: list[dict[str, Any]] = []
        for item in call.data[ATTR_COMMANDS]:
            command_id = item[ATTR_COMMAND_ID]
            command = item[ATTR_COMMAND]
            _LOGGER.debug("Sending ZONT bulk command id=%s", command_id)
            response = await _async_send(client, command_id, command)
            responses.append({"id": command_id, "response": response})
        if call.return_response:
            return {"responses": responses}
        return None

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        async_handle_send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_BULK,
        async_handle_send_bulk,
        schema=SEND_BULK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _get_loaded_client(hass: HomeAssistant) -> ZontClient:
    """Return the singleton loaded client or raise a translated error."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_configured",
        )

    entry = entries[0]
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
        )

    typed_entry = cast(ConfigEntry[ZontRuntimeData], entry)
    return typed_entry.runtime_data.client


async def _async_send(
    client: ZontClient,
    command_id: int,
    command: str,
) -> dict[str, Any]:
    """Send a command and translate client failures for the HA UI."""
    try:
        return await client.async_send_command(command_id, command)
    except asyncio.CancelledError:
        raise
    except ZontCommandTimeoutError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_timeout",
            translation_placeholders={"id": str(command_id)},
        ) from err
    except ZontConnectionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="controller_offline",
        ) from err
    except ZontProtocolError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="protocol_error",
        ) from err
