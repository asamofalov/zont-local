"""Climate entities for ZONT consumer heating circuits."""

from __future__ import annotations

import asyncio
from math import isfinite
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
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
from .heating_config import (
    AIR_MAX_TEMPERATURE,
    AIR_MIN_TEMPERATURE,
    CONSUMER_CIRCUIT_SUBTYPE,
    ZontHeatingCircuitControlData,
)
from .objects import ZontHeatingCircuitData, ZontHeatingCircuitMode

TARGET_TEMPERATURE_STEP = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT consumer heating circuits."""
    known_entities: set[int] = set()

    @callback
    def async_add_object_entities() -> None:
        """Add climate entities for newly discovered consumer circuits."""
        new_entities: list[ClimateEntity] = []
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if (
                not isinstance(obj, ZontHeatingCircuitData)
                or obj.subtype != CONSUMER_CIRCUIT_SUBTYPE
                or obj.object_id in known_entities
            ):
                continue
            known_entities.add(obj.object_id)
            new_entities.append(ZontConsumerClimate(entry, obj.object_id))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        entry.runtime_data.coordinator.async_add_listener(async_add_object_entities)
    )
    async_add_object_entities()


class ZontConsumerClimate(ZontObjectCoordinatorEntity, ClimateEntity):
    """Represent one ZONT consumer heating circuit."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TARGET_TEMPERATURE_STEP
    _attr_hvac_modes: list[HVACMode] = []

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a ZONT consumer climate entity."""
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "climate", None)

    @property
    def _circuit(self) -> ZontHeatingCircuitData | None:
        """Return the current consumer-circuit snapshot."""
        obj = self.object_data
        return (
            obj
            if isinstance(obj, ZontHeatingCircuitData)
            and obj.subtype == CONSUMER_CIRCUIT_SUBTYPE
            else None
        )

    @property
    def _control(self) -> ZontHeatingCircuitControlData | None:
        """Return the latest resolved control metadata."""
        return self.coordinator.data.heating_controls.get(self._object_id)

    @property
    def available(self) -> bool:
        """Return whether the consumer circuit is available."""
        return super().available and self._circuit is not None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Expose target control only after a safe range is known."""
        control = self._control
        if control is not None and control.can_set_temperature:
            return ClimateEntityFeature.TARGET_TEMPERATURE
        return ClimateEntityFeature(0)

    @property
    def current_temperature(self) -> float | None:
        """Return the current circuit temperature."""
        circuit = self._circuit
        return circuit.current_temperature if circuit is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target circuit temperature."""
        circuit = self._circuit
        return circuit.target_temperature if circuit is not None else None

    @property
    def min_temp(self) -> float:
        """Return the resolved minimum setpoint."""
        control = self._control
        return (
            control.min_temperature
            if control is not None and control.min_temperature is not None
            else AIR_MIN_TEMPERATURE
        )

    @property
    def max_temp(self) -> float:
        """Return the resolved maximum setpoint."""
        control = self._control
        return (
            control.max_temperature
            if control is not None and control.max_temperature is not None
            else AIR_MAX_TEMPERATURE
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the observed controller mode without offering mode control."""
        circuit = self._circuit
        if circuit is None or circuit.mode is None:
            return None
        return {
            ZontHeatingCircuitMode.HEAT: HVACMode.HEAT,
            ZontHeatingCircuitMode.COOL: HVACMode.COOL,
            ZontHeatingCircuitMode.OFF: HVACMode.OFF,
        }[circuit.mode]

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the consumer-circuit target temperature."""
        control = self._control
        if control is None or not control.can_set_temperature:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_control_unavailable",
            )

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if (
            not isinstance(temperature, int | float)
            or isinstance(temperature, bool)
            or not isfinite(temperature)
            or not self.min_temp <= temperature <= self.max_temp
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_out_of_range",
                translation_placeholders={
                    "min_temperature": str(self.min_temp),
                    "max_temperature": str(self.max_temp),
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
