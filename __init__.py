from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_URL, CONF_USER, CONF_PASS
from .client import ZontWsClient, ZontCredentials
from .services import async_register_services

PLATFORMS: list[str] = []  # мы пока не создаём entity, только сервисы

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    url = entry.data[CONF_URL]
    creds = ZontCredentials(user=entry.data[CONF_USER], password=entry.data[CONF_PASS])

    client = ZontWsClient(hass=hass, session=session, url=url, creds=creds)
    await client.start()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["client"] = client

    await async_register_services(hass, client)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client: ZontWsClient = hass.data[DOMAIN]["client"]
    await client.stop()
    hass.data.pop(DOMAIN, None)
    return True