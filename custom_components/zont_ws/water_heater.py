"""Water heater entities for ZONT heating circuits."""

from __future__ import annotations

import asyncio
from math import isfinite
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import (
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from .const import DOMAIN
from .coordinator import ZontRuntimeData
from .entity import ZontObjectCoordinatorEntity
from .heating import (
    ZontCommandRejectedError,
    async_set_heating_circuit_temperature_and_refresh,
)
from .heating_config import DHW_CIRCUIT_SUBTYPE
from .objects import ZontHeatingCircuitData

MIN_TARGET_TEMPERATURE = 5.0
MAX_TARGET_TEMPERATURE = 75.0
TARGET_TEMPERATURE_STEP = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT hot water circuits."""
    known_entities: set[int] = set()

    @callback
    def async_add_object_entities() -> None:
        """Add entities for newly discovered hot water circuits."""
        new_entities: list[WaterHeaterEntity] = []
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if (
                not isinstance(obj, ZontHeatingCircuitData)
                or obj.subtype != DHW_CIRCUIT_SUBTYPE
                or obj.object_id in known_entities
            ):
                continue
            known_entities.add(obj.object_id)
            new_entities.append(ZontDhwWaterHeater(entry, obj.object_id))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        entry.runtime_data.coordinator.async_add_listener(async_add_object_entities)
    )
    async_add_object_entities()


class ZontDhwWaterHeater(ZontObjectCoordinatorEntity, WaterHeaterEntity):
    """Represent one ZONT domestic hot water circuit."""

    _attr_name = None
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TARGET_TEMPERATURE
    _attr_max_temp = MAX_TARGET_TEMPERATURE
    _attr_target_temperature_step = TARGET_TEMPERATURE_STEP

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a ZONT hot water entity."""
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "water_heater", None)

    @property
    def _circuit(self) -> ZontHeatingCircuitData | None:
        """Return the current hot water circuit snapshot."""
        obj = self.object_data
        return (
            obj
            if isinstance(obj, ZontHeatingCircuitData)
            and obj.subtype == DHW_CIRCUIT_SUBTYPE
            else None
        )

    @property
    def available(self) -> bool:
        """Return whether the hot water circuit is available."""
        return super().available and self._circuit is not None

    @property
    def current_temperature(self) -> float | None:
        """Return the current hot water temperature."""
        circuit = self._circuit
        return circuit.current_temperature if circuit is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target hot water temperature."""
        circuit = self._circuit
        return circuit.target_temperature if circuit is not None else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the hot water target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if (
            not isinstance(temperature, int | float)
            or isinstance(temperature, bool)
            or not isfinite(temperature)
            or not MIN_TARGET_TEMPERATURE <= temperature <= MAX_TARGET_TEMPERATURE
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_out_of_range",
                translation_placeholders={
                    "min_temperature": str(MIN_TARGET_TEMPERATURE),
                    "max_temperature": str(MAX_TARGET_TEMPERATURE),
                },
            )

        try:
            await async_set_heating_circuit_temperature_and_refresh(
                self._client,
                self.coordinator,
                self._object_id,
                float(temperature),
            )
        except asyncio.CancelledError:
            raise
        except ZontCommandRejectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontCommandTimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_timeout",
                translation_placeholders={"id": str(self._object_id)},
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
