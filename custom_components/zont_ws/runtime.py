"""Runtime resources owned by one ZONT config entry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .client import ZontWsClient
from .object_platform import ZontObjectEntityManager

if TYPE_CHECKING:
    from .coordinator import ZontDataUpdateCoordinator
    from .object_export import ZontTemperatureExportManager


@dataclass(slots=True)
class ZontRuntimeData:
    """Collect resources and mutable settings for one config entry."""

    client: ZontWsClient
    coordinator: ZontDataUpdateCoordinator
    export_manager: ZontTemperatureExportManager | None = None
    object_entities: ZontObjectEntityManager = field(
        default_factory=ZontObjectEntityManager
    )
    options: dict[str, Any] = field(default_factory=dict)
    connection_settings: tuple[str, str, str] | None = None
    controller_device_id: str | None = None
    options_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def async_shutdown(self) -> None:
        """Stop every config-entry-owned resource in dependency order."""
        try:
            await self.object_entities.async_shutdown()
        finally:
            try:
                if self.export_manager is not None:
                    await self.export_manager.async_shutdown()
            finally:
                try:
                    await self.coordinator.async_shutdown()
                finally:
                    await self.client.async_stop()
