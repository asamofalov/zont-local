"""Binary sensor platform for the ZONT integration."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entities.analog_input import ZontAnalogInputTriggeredBinarySensor
from .entities.controller import (
    ZontCloudConnectedBinarySensor,
    ZontConnectedBinarySensor,
)
from .entities.heating.diagnostics import (
    HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE,
    ZontHeatingCircuitBinarySensor,
)
from .entities.mixer import MIXER_BINARY_SENSOR_DESCRIPTIONS, ZontMixerBinarySensor
from .entities.pump import ZontPumpRunningBinarySensor
from .entities.radio import RADIO_TRIGGER_DEVICE_CLASSES, ZontRadioTriggeredBinarySensor
from .entities.relay import ZontRelayFailedBinarySensor
from .object_import import object_import_configuration
from .object_platform import ZontObjectEntityReconciler
from .protocol.objects import (
    ZontAnalogInputData,
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontPumpData,
    ZontRadioSensorData,
    ZontRelayData,
)
from .runtime import ZontRuntimeData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT binary sensors."""
    async_add_entities(
        [
            ZontConnectedBinarySensor(entry),
            ZontCloudConnectedBinarySensor(entry),
        ]
    )

    @callback
    def object_entity_factories() -> dict[
        tuple[int, str], Callable[[], BinarySensorEntity]
    ]:
        """Describe binary sensors selected by the current import options."""
        factories: dict[tuple[int, str], Callable[[], BinarySensorEntity]] = {}
        import_configuration = object_import_configuration(entry.options)
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if not import_configuration.imports(obj.object_id):
                continue
            if isinstance(obj, ZontAnalogInputData):
                identity = (obj.object_id, "analog_triggered")
                factories[identity] = partial(
                    ZontAnalogInputTriggeredBinarySensor,
                    entry,
                    obj.object_id,
                    obj.subtype,
                )
                continue
            if isinstance(obj, ZontHeatingCircuitData):
                descriptions = (
                    HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE.get(
                        obj.subtype,
                        (),
                    )
                )
                for description in descriptions:
                    identity = (obj.object_id, description.key)
                    factories[identity] = partial(
                        ZontHeatingCircuitBinarySensor,
                        entry,
                        obj.object_id,
                        description,
                    )
                continue
            if isinstance(obj, ZontPumpData):
                identity = (obj.object_id, "running")
                factories[identity] = partial(
                    ZontPumpRunningBinarySensor,
                    entry,
                    obj.object_id,
                )
                continue
            if isinstance(obj, ZontMixerData):
                for description in MIXER_BINARY_SENSOR_DESCRIPTIONS:
                    identity = (obj.object_id, description.key)
                    factories[identity] = partial(
                        ZontMixerBinarySensor,
                        entry,
                        obj.object_id,
                        description,
                    )
                continue
            if isinstance(obj, ZontRelayData):
                identity = (obj.object_id, "failed")
                factories[identity] = partial(
                    ZontRelayFailedBinarySensor,
                    entry,
                    obj.object_id,
                )
                continue
            if (
                not isinstance(obj, ZontRadioSensorData)
                or obj.subtype not in RADIO_TRIGGER_DEVICE_CLASSES
            ):
                continue
            identity = (obj.object_id, "radio_triggered")
            factories[identity] = partial(
                ZontRadioTriggeredBinarySensor,
                entry,
                obj.object_id,
                obj.subtype,
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
