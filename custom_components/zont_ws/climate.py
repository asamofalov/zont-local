"""Climate entities for ZONT consumer heating circuits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
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
from .const import CONF_HEATING_OFF_MODE_ID, DOMAIN
from .entity import ZontObjectCoordinatorEntity
from .heating import (
    ZontCommandRejectedError,
    ZontCommandStateError,
    async_apply_heating_mode_and_refresh,
    async_set_heating_circuit_temperature_and_refresh,
)
from .heating_config import (
    AIR_MAX_TEMPERATURE,
    AIR_MIN_TEMPERATURE,
    CONSUMER_CIRCUIT_SUBTYPE,
    ZontHeatingCircuitControlData,
)
from .heating_modes import (
    mode_is_applicable_to_circuit,
    validated_off_mode_id,
)
from .object_import import object_import_configuration
from .object_platform import ZontObjectEntityReconciler
from .objects import ZontHeatingCircuitData, ZontHeatingCircuitMode
from .runtime import ZontRuntimeData

TARGET_TEMPERATURE_STEP = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT consumer heating circuits."""

    @callback
    def object_entity_factories() -> dict[int, Callable[[], ClimateEntity]]:
        """Describe climate entities selected by current import options."""
        factories: dict[int, Callable[[], ClimateEntity]] = {}
        import_configuration = object_import_configuration(entry.options)
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if (
                not import_configuration.imports(obj.object_id)
                or not isinstance(obj, ZontHeatingCircuitData)
                or obj.subtype != CONSUMER_CIRCUIT_SUBTYPE
            ):
                continue
            factories[obj.object_id] = partial(
                ZontConsumerClimate,
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


class ZontConsumerClimate(ZontObjectCoordinatorEntity, ClimateEntity):
    """Represent one ZONT consumer heating circuit."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TARGET_TEMPERATURE_STEP

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a ZONT consumer climate entity."""
        self._entry = entry
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "climate", None)
        self._last_active_mode_id: int | None = None
        self._last_active_target: float | None = None
        self._remember_active_state()

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
        """Expose only controls confirmed by current controller metadata."""
        features = ClimateEntityFeature(0)
        control = self._control
        if control is not None and control.can_set_temperature:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._off_mode_id is not None and self._can_turn_on:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the standard modes supported by the configured binding."""
        if self._off_mode_id is None or not self._can_turn_on:
            return []
        return [HVACMode.HEAT, HVACMode.OFF]

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
    def _can_turn_on(self) -> bool:
        """Return whether an active state can be restored safely."""
        circuit = self._circuit
        if circuit is None or circuit.mode not in (
            ZontHeatingCircuitMode.HEAT,
            ZontHeatingCircuitMode.OFF,
        ):
            return False
        if self._restorable_mode_id is not None:
            return True
        control = self._control
        return control is not None and control.can_set_temperature

    @property
    def _restorable_mode_id(self) -> int | None:
        """Return the remembered non-off mode when it is still valid."""
        mode_id = self._last_active_mode_id
        if mode_id is None:
            return None
        mode = self.coordinator.data.heating_modes.get(mode_id)
        if (
            mode is None
            or mode.circuit_targets.get(self._object_id) in (None, 0)
            or not mode_is_applicable_to_circuit(
                mode_id,
                self._object_id,
                self.coordinator.data.heating_states,
            )
        ):
            return None
        return mode_id

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember the last active state before writing the new HA state."""
        self._remember_active_state()
        super()._handle_coordinator_update()

    @callback
    def _remember_active_state(self) -> None:
        """Cache the last observed active mode and target for this HA session."""
        circuit = self._circuit
        if circuit is None or circuit.mode is ZontHeatingCircuitMode.OFF:
            return
        self._last_active_mode_id = (
            circuit.mode_id
            if circuit.mode_id is not None and circuit.mode_id > 0
            else None
        )
        if circuit.target_temperature is not None and isfinite(
            circuit.target_temperature
        ):
            self._last_active_target = circuit.target_temperature

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn the circuit on or off through the configured binding."""
        if hvac_mode is HVACMode.OFF:
            await self.async_turn_off()
            return
        if hvac_mode is HVACMode.HEAT:
            await self.async_turn_on()
            return
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="heating_hvac_mode_unavailable",
        )

    async def async_turn_off(self) -> None:
        """Apply the configured all-off mode only to this circuit."""
        circuit = self._circuit
        if circuit is not None and circuit.mode is ZontHeatingCircuitMode.OFF:
            return
        mode_id = self._off_mode_id
        if mode_id is None or not self._can_turn_on:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="heating_off_mode_unavailable",
            )
        self._remember_active_state()
        await self._async_apply_mode(mode_id, expect_off=True)

    async def async_turn_on(self) -> None:
        """Restore the previous mode, setpoint, or minimum safe setpoint."""
        circuit = self._circuit
        if circuit is not None and circuit.mode is not ZontHeatingCircuitMode.OFF:
            return
        if self._off_mode_id is None or not self._can_turn_on:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="heating_turn_on_unavailable",
            )

        if (mode_id := self._restorable_mode_id) is not None:
            await self._async_apply_mode(mode_id, expect_off=False)
            return

        target = self._last_active_target
        if target is None or not self.min_temp <= target <= self.max_temp:
            target = self.min_temp
        await self.async_set_temperature(**{ATTR_TEMPERATURE: target})

    async def _async_apply_mode(self, mode_id: int, *, expect_off: bool) -> None:
        """Apply one mode and translate protocol failures for Home Assistant."""
        try:
            await async_apply_heating_mode_and_refresh(
                self._client,
                self.coordinator,
                self._object_id,
                mode_id,
                expect_off=expect_off,
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
