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
    ZontEthernetConnectedBinarySensor,
    ZontWifiConnectedBinarySensor,
)
from .entities.digital_bus import ZontDigitalBusFaultBinarySensor
from .entities.heating.states import (
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
    ZontDigitalBusAdapterData,
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

    added_controller_entities: set[str] = set()

    @callback
    def add_supported_controller_entities() -> None:
        """Add optional connection sensors after their source is supported."""
        controller = entry.runtime_data.coordinator.data.controller
        factories: tuple[tuple[str, bool, Callable[[], BinarySensorEntity]], ...] = (
            (
                "wifi_connected",
                controller.wifi_status is not None,
                partial(ZontWifiConnectedBinarySensor, entry),
            ),
            (
                "ethernet_connected",
                controller.ethernet_status is not None,
                partial(ZontEthernetConnectedBinarySensor, entry),
            ),
        )
        new_factories = [
            (key, factory)
            for key, supported, factory in factories
            if supported and key not in added_controller_entities
        ]
        if not new_factories:
            return
        added_controller_entities.update(key for key, _factory in new_factories)
        async_add_entities([factory() for _key, factory in new_factories])

    add_supported_controller_entities()
    entry.async_on_unload(
        entry.runtime_data.coordinator.async_add_listener(
            add_supported_controller_entities
        )
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
            if isinstance(obj, ZontDigitalBusAdapterData):
                if obj.has_fault is not None:
                    identity = (obj.object_id, "fault")
                    factories[identity] = partial(
                        ZontDigitalBusFaultBinarySensor,
                        entry,
                        obj.object_id,
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
