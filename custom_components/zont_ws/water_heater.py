"""Water heater entities for ZONT heating circuits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from math import isfinite
from typing import Any

from homeassistant.components.water_heater import (
    STATE_OFF,
    STATE_ON,
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
from .const import (
    CONF_DHW_ON_TEMPERATURE,
    CONF_HEATING_OFF_MODE_ID,
    DHW_DEFAULT_ON_TEMPERATURE,
    DHW_MAX_TARGET_TEMPERATURE,
    DHW_MIN_TARGET_TEMPERATURE,
    DOMAIN,
)
from .coordinator import ZontRuntimeData
from .entity import ZontObjectCoordinatorEntity
from .heating import (
    ZontCommandRejectedError,
    ZontCommandStateError,
    async_apply_heating_mode_and_refresh,
    async_set_heating_circuit_temperature_and_confirm_active,
    async_set_heating_circuit_temperature_and_refresh,
)
from .heating_config import DHW_CIRCUIT_SUBTYPE
from .heating_modes import validated_off_mode_id
from .object_import import object_import_configuration
from .object_platform import ZontObjectEntityReconciler
from .objects import ZontHeatingCircuitData, ZontHeatingCircuitMode

MIN_TARGET_TEMPERATURE = DHW_MIN_TARGET_TEMPERATURE
MAX_TARGET_TEMPERATURE = DHW_MAX_TARGET_TEMPERATURE
TARGET_TEMPERATURE_STEP = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT hot water circuits."""

    @callback
    def object_entity_factories() -> dict[int, Callable[[], WaterHeaterEntity]]:
        """Describe water heaters selected by current import options."""
        factories: dict[int, Callable[[], WaterHeaterEntity]] = {}
        import_configuration = object_import_configuration(entry.options)
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if (
                not import_configuration.imports(obj.object_id)
                or not isinstance(obj, ZontHeatingCircuitData)
                or obj.subtype != DHW_CIRCUIT_SUBTYPE
            ):
                continue
            factories[obj.object_id] = partial(
                ZontDhwWaterHeater,
                entry,
                obj.object_id,
            )
        return factories

    reconciler = ZontObjectEntityReconciler(
        hass,
        entry,
        async_add_entities,
        object_entity_factories,
    )
    entry.async_on_unload(entry.runtime_data.object_entities.async_register(reconciler))
    entry.async_on_unload(
        entry.runtime_data.coordinator.async_add_listener(
            reconciler.async_schedule_reconcile
        )
    )
    await reconciler.async_reconcile()


class ZontDhwWaterHeater(ZontObjectCoordinatorEntity, WaterHeaterEntity):
    """Represent one ZONT domestic hot water circuit."""

    _attr_name = None
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
        self._entry = entry
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "water_heater", None)
        self._last_active_target: float | None = None
        self._remember_active_target()

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
    def supported_features(self) -> WaterHeaterEntityFeature:
        """Expose standard on/off only for a validated controller binding."""
        features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
        circuit = self._circuit
        if (
            self._off_mode_id is not None
            and circuit is not None
            and circuit.mode
            in (
                ZontHeatingCircuitMode.HEAT,
                ZontHeatingCircuitMode.OFF,
            )
        ):
            features |= WaterHeaterEntityFeature.ON_OFF
        return features

    @property
    def current_operation(self) -> str | None:
        """Map the observed ZONT state to standard water-heater states."""
        circuit = self._circuit
        if circuit is None:
            return None
        return {
            ZontHeatingCircuitMode.HEAT: STATE_ON,
            ZontHeatingCircuitMode.OFF: STATE_OFF,
        }.get(circuit.mode)

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

    @property
    def _off_mode_id(self) -> int | None:
        """Return the selected off mode when it remains safe and applicable."""
        return validated_off_mode_id(
            self._entry.options.get(CONF_HEATING_OFF_MODE_ID),
            self._object_id,
            self.coordinator.data.objects,
            self.coordinator.data.heating_states,
            self.coordinator.data.heating_modes,
        )

    @property
    def _configured_on_temperature(self) -> float:
        """Return the configured restart-safe DHW target."""
        value = self._entry.options.get(
            CONF_DHW_ON_TEMPERATURE,
            DHW_DEFAULT_ON_TEMPERATURE,
        )
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and isfinite(value)
            and MIN_TARGET_TEMPERATURE <= value <= MAX_TARGET_TEMPERATURE
        ):
            return float(value)
        return DHW_DEFAULT_ON_TEMPERATURE

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember an active target before writing the new HA state."""
        self._remember_active_target()
        super()._handle_coordinator_update()

    @callback
    def _remember_active_target(self) -> None:
        """Cache the latest active DHW target for this HA session."""
        circuit = self._circuit
        if (
            circuit is None
            or circuit.mode is not ZontHeatingCircuitMode.HEAT
            or circuit.target_temperature is None
            or not isfinite(circuit.target_temperature)
            or not MIN_TARGET_TEMPERATURE
            <= circuit.target_temperature
            <= MAX_TARGET_TEMPERATURE
        ):
            return
        self._last_active_target = circuit.target_temperature

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Apply the configured all-off mode only to this DHW circuit."""
        circuit = self._circuit
        if circuit is not None and circuit.mode is ZontHeatingCircuitMode.OFF:
            return
        mode_id = self._off_mode_id
        if mode_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="heating_off_mode_unavailable",
            )
        self._remember_active_target()
        try:
            await async_apply_heating_mode_and_refresh(
                self._client,
                self.coordinator,
                self._object_id,
                mode_id,
                expect_off=True,
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
        except ZontCommandStateError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="heating_state_not_confirmed",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protocol_error",
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Restore the previous or configured DHW target."""
        circuit = self._circuit
        if circuit is not None and circuit.mode is ZontHeatingCircuitMode.HEAT:
            return
        if self._off_mode_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="heating_turn_on_unavailable",
            )

        target = self._last_active_target or self._configured_on_temperature
        try:
            await async_set_heating_circuit_temperature_and_confirm_active(
                self._client,
                self.coordinator,
                self._object_id,
                target,
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
        except ZontCommandStateError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="heating_state_not_confirmed",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protocol_error",
            ) from err

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
