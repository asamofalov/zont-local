"""Button platform for the ZONT integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entities.controller import ZontRestartButton
from .runtime import ZontRuntimeData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZONT buttons."""
    async_add_entities([ZontRestartButton(entry)])
