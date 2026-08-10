"""Sensor platform for the ZONT integration."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entities.analog_input import ZontAnalogInputValueSensor
from .entities.controller import ZontConnectionChannelSensor, ZontSupplyVoltageSensor
from .entities.digital_bus import (
    DIGITAL_BUS_SENSOR_DESCRIPTIONS,
    ZontDigitalBusSensor,
)
from .entities.heating.diagnostics import ZontHeatingControlModeSensor
from .entities.mixer import ZontMixerStateSensor
from .entities.radio import (
    RADIO_SENSOR_DESCRIPTIONS,
    RADIO_SENSOR_FIELDS_BY_SUBTYPE,
    ZontRadioSensor,
)
from .entities.temperature import (
    ZontDigitalTemperatureSensor,
    ZontNtcTemperatureSensor,
)
from .object_import import object_import_configuration
from .object_platform import ZontObjectEntityReconciler
from .protocol.heating_config import CONSUMER_CIRCUIT_SUBTYPE
from .protocol.objects import (
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontNtcTemperatureSensorData,
    ZontRadioSensorData,
)
from .runtime import ZontRuntimeData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ZontRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZONT sensors."""
    async_add_entities(
        [
            ZontConnectionChannelSensor(entry),
            ZontSupplyVoltageSensor(entry),
        ]
    )

    @callback
    def object_entity_factories() -> dict[tuple[int, str], Callable[[], SensorEntity]]:
        """Describe sensor entities selected by the current import options."""
        factories: dict[tuple[int, str], Callable[[], SensorEntity]] = {}
        import_configuration = object_import_configuration(entry.options)
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if not import_configuration.imports(obj.object_id):
                continue
            if isinstance(obj, ZontAnalogInputData):
                identity = (obj.object_id, "value")
                factories[identity] = partial(
                    ZontAnalogInputValueSensor,
                    entry,
                    obj.object_id,
                    obj.subtype,
                )
                continue
            if isinstance(obj, ZontDigitalTemperatureSensorData):
                identity = (obj.object_id, "temperature")
                factories[identity] = partial(
                    ZontDigitalTemperatureSensor,
                    entry,
                    obj.object_id,
                )
                continue
            if isinstance(obj, ZontNtcTemperatureSensorData):
                identity = (obj.object_id, "temperature")
                factories[identity] = partial(
                    ZontNtcTemperatureSensor,
                    entry,
                    obj.object_id,
                )
                continue
            if isinstance(obj, ZontRadioSensorData):
                for field in RADIO_SENSOR_FIELDS_BY_SUBTYPE.get(obj.subtype, ()):
                    identity = (obj.object_id, field)
                    factories[identity] = partial(
                        ZontRadioSensor,
                        entry,
                        obj.object_id,
                        RADIO_SENSOR_DESCRIPTIONS[field],
                    )
                continue
            if (
                isinstance(obj, ZontHeatingCircuitData)
                and obj.subtype == CONSUMER_CIRCUIT_SUBTYPE
            ):
                identity = (obj.object_id, "control_mode")
                factories[identity] = partial(
                    ZontHeatingControlModeSensor,
                    entry,
                    obj.object_id,
                )
                continue
            if isinstance(obj, ZontMixerData):
                identity = (obj.object_id, "state")
                factories[identity] = partial(
                    ZontMixerStateSensor,
                    entry,
                    obj.object_id,
                )
                continue
            if isinstance(obj, ZontDigitalBusAdapterData) and obj.available:
                for description in DIGITAL_BUS_SENSOR_DESCRIPTIONS:
                    identity = (obj.object_id, description.key)
                    if description.value_fn(obj) is None:
                        continue
                    factories[identity] = partial(
                        ZontDigitalBusSensor,
                        entry,
                        obj.object_id,
                        description,
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
