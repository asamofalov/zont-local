from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, CONF_URL, CONF_USER, CONF_PASS, PLATFORMS
from .client import ZontWsClient, ZontCredentials
from .services import async_register_services


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    url = entry.data[CONF_URL]
    creds = ZontCredentials(user=entry.data[CONF_USER], password=entry.data[CONF_PASS])

    # 1) Создаём hub-device (контроллер)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="ZONT Controller",
        manufacturer="ZONT",
        model="WebSocket Controller",
    )

    # 2) Стартуем клиента
    client = ZontWsClient(hass=hass, session=session, url=url, creds=creds)
    await client.start()

    # 3) Сохраняем данные по entry_id
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"client": client}

    # 4) Регистрируем сервисы
    await async_register_services(hass, client)

    # 5) Поднимаем платформы (пока только binary_sensor)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # выгружаем платформы
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        client: ZontWsClient = data["client"]
        await client.stop()

    if hass.data.get(DOMAIN) == {}:
        hass.data.pop(DOMAIN, None)

    return unload_ok