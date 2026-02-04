from __future__ import annotations

import logging
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, SERVICE_SEND_COMMAND, SERVICE_SEND_BULK

_LOGGER = logging.getLogger(__name__)

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("id"): vol.Coerce(int),
        vol.Required("cmd"): vol.Any(str, int, float),
    }
)

SEND_BULK_SCHEMA = vol.Schema(
    {
        vol.Required("commands"): [
            {
                vol.Required("id"): vol.Coerce(int),
                vol.Required("cmd"): vol.Any(str, int, float),
            }
        ]
    }
)

async def async_register_services(hass: HomeAssistant, client) -> None:
    async def handle_send_command(call: ServiceCall):
        data = SEND_COMMAND_SCHEMA(dict(call.data))
        _LOGGER.info("zont_ws.send_command request: %s", data)
        resp = await client.send_command(data["id"], str(data["cmd"]))
        _LOGGER.info("zont_ws.send_command response: %s", resp)

    async def handle_send_bulk(call: ServiceCall):
        data = SEND_BULK_SCHEMA(dict(call.data))
        for item in data["commands"]:
            _LOGGER.info("zont_ws.send_bulk request: %s", item)
            resp = await client.send_command(item["id"], str(item["cmd"]))
            _LOGGER.info("zont_ws.send_bulk response id=%s: %s", item["id"], resp)

    hass.services.async_register(DOMAIN, SERVICE_SEND_COMMAND, handle_send_command)
    hass.services.async_register(DOMAIN, SERVICE_SEND_BULK, handle_send_bulk)