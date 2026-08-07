"""ZONT WebSocket integration."""

from __future__ import annotations

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    ZontWsClient,
)
from .const import (
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    LEGACY_CONF_PASSWORD,
    LEGACY_CONF_USERNAME,
    PLATFORMS,
)
from .services import async_setup_services

type ZontConfigEntry = ConfigEntry[ZontWsClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level service actions."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Migrate legacy configuration keys without requiring reconfiguration."""
    if entry.version > CONFIG_ENTRY_VERSION:
        return False

    if entry.version == 1:
        data = dict(entry.data)
        if CONF_USERNAME not in data and LEGACY_CONF_USERNAME in data:
            data[CONF_USERNAME] = data.pop(LEGACY_CONF_USERNAME)
        if CONF_PASSWORD not in data and LEGACY_CONF_PASSWORD in data:
            data[CONF_PASSWORD] = data.pop(LEGACY_CONF_PASSWORD)
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            version=CONFIG_ENTRY_VERSION,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Set up ZONT from a config entry."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="ZONT Controller",
        manufacturer="ZONT",
        model="WebSocket Controller",
    )

    client = ZontWsClient(
        hass=hass,
        session=async_get_clientsession(hass),
        url=entry.data[CONF_URL],
        credentials=ZontCredentials(
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
        ),
        entry_id=entry.entry_id,
        device_id=device.id,
        on_authentication_error=lambda: entry.async_start_reauth(hass),
    )

    try:
        await client.async_start()
    except ZontAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
        ) from err
    except (ZontConnectionError, ZontProtocolError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err

    entry.runtime_data = client
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await client.async_stop()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Unload a ZONT config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.async_stop()
    return True
