"""Binary sensors for the ZONT WebSocket integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import connection_signal
from .coordinator import ZontRuntimeData
from .entity import (
    ZontCoordinatorEntity,
    ZontEntityMixin,
    ZontObjectCoordinatorEntity,
)
from .heating_config import (
    CONSUMER_CIRCUIT_SUBTYPE,
    DHW_CIRCUIT_SUBTYPE,
    ZontHeatingCircuitControlData,
    ZontHeatingCircuitInternalState,
)
from .mixer import ZontMixerInternalState
from .objects import (
    ZontAnalogInputData,
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontPumpData,
    ZontRadioSensorData,
    ZontRelayData,
)
from .relay import ZontRelayInternalState

ANALOG_TRIGGER_DEVICE_CLASSES: dict[int, BinarySensorDeviceClass | None] = {
    3: BinarySensorDeviceClass.DOOR,
    4: BinarySensorDeviceClass.MOTION,
    5: BinarySensorDeviceClass.SMOKE,
    6: BinarySensorDeviceClass.MOISTURE,
    7: BinarySensorDeviceClass.MOTION,
    9: BinarySensorDeviceClass.PROBLEM,
    10: BinarySensorDeviceClass.PROBLEM,
    11: BinarySensorDeviceClass.POWER,
    14: None,
    15: BinarySensorDeviceClass.SAFETY,
    19: None,
    20: None,
}

RADIO_TRIGGER_DEVICE_CLASSES = {
    10: BinarySensorDeviceClass.MOISTURE,
    11: BinarySensorDeviceClass.MOTION,
}


@dataclass(frozen=True, kw_only=True)
class ZontHeatingCircuitBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe one heating-circuit binary state."""

    value_fn: Callable[
        [
            ZontHeatingCircuitData,
            ZontHeatingCircuitControlData | None,
            ZontHeatingCircuitInternalState | None,
        ],
        bool | None,
    ]


CONSUMER_HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS = (
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="weather_compensation",
        translation_key="heating_weather_compensation",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda circuit, control, state: (
            control.has_weather_compensation if control is not None else None
        ),
    ),
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="blocked",
        translation_key="heating_blocked",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda circuit, control, state: (
            state.is_blocked if state is not None else None
        ),
    ),
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="sensor_fault",
        translation_key="heating_sensor_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda circuit, control, state: (
            state.has_sensor_fault if state is not None else None
        ),
    ),
    ZontHeatingCircuitBinarySensorEntityDescription(
        key="summer_mode",
        translation_key="heating_summer_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda circuit, control, state: (
            state.is_summer_mode if state is not None else None
        ),
    ),
)

HEATING_CIRCUIT_FAULT_DESCRIPTION = ZontHeatingCircuitBinarySensorEntityDescription(
    key="fault",
    translation_key="heating_fault",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda circuit, control, state: circuit.fault,
)

HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS_BY_SUBTYPE = {
    DHW_CIRCUIT_SUBTYPE: (HEATING_CIRCUIT_FAULT_DESCRIPTION,),
    CONSUMER_CIRCUIT_SUBTYPE: (
        *CONSUMER_HEATING_CIRCUIT_BINARY_SENSOR_DESCRIPTIONS,
        HEATING_CIRCUIT_FAULT_DESCRIPTION,
    ),
}


@dataclass(frozen=True, kw_only=True)
class ZontMixerBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe one diagnostic mixer flag."""

    value_fn: Callable[[ZontMixerInternalState], bool]


MIXER_BINARY_SENSOR_DESCRIPTIONS = (
    ZontMixerBinarySensorEntityDescription(
        key="sensor_fault",
        translation_key="mixer_sensor_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.has_sensor_fault,
    ),
    ZontMixerBinarySensorEntityDescription(
        key="output_fault",
        translation_key="mixer_output_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.has_output_fault,
    ),
    ZontMixerBinarySensorEntityDescription(
        key="set_failed",
        translation_key="mixer_set_failed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.has_set_failed,
    ),
)


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

    known_entities: set[tuple[int, str]] = set()

    @callback
    def async_add_object_entities() -> None:
        """Add binary sensor entities for newly discovered objects."""
        new_entities: list[BinarySensorEntity] = []
        for obj in entry.runtime_data.coordinator.data.objects.values():
            if isinstance(obj, ZontAnalogInputData):
                identity = (obj.object_id, "analog_triggered")
                if identity in known_entities:
                    continue
                known_entities.add(identity)
                new_entities.append(
                    ZontAnalogInputTriggeredBinarySensor(
                        entry,
                        obj.object_id,
                        obj.subtype,
                    )
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
                    if identity in known_entities:
                        continue
                    known_entities.add(identity)
                    new_entities.append(
                        ZontHeatingCircuitBinarySensor(
                            entry,
                            obj.object_id,
                            description,
                        )
                    )
                continue
            if isinstance(obj, ZontPumpData):
                identity = (obj.object_id, "running")
                if identity in known_entities:
                    continue
                known_entities.add(identity)
                new_entities.append(ZontPumpRunningBinarySensor(entry, obj.object_id))
                continue
            if isinstance(obj, ZontMixerData):
                for description in MIXER_BINARY_SENSOR_DESCRIPTIONS:
                    identity = (obj.object_id, description.key)
                    if identity in known_entities:
                        continue
                    known_entities.add(identity)
                    new_entities.append(
                        ZontMixerBinarySensor(
                            entry,
                            obj.object_id,
                            description,
                        )
                    )
                continue
            if isinstance(obj, ZontRelayData):
                identity = (obj.object_id, "failed")
                if identity in known_entities:
                    continue
                known_entities.add(identity)
                new_entities.append(ZontRelayFailedBinarySensor(entry, obj.object_id))
                continue
            if (
                not isinstance(obj, ZontRadioSensorData)
                or obj.subtype not in RADIO_TRIGGER_DEVICE_CLASSES
            ):
                continue
            identity = (obj.object_id, "radio_triggered")
            if identity in known_entities:
                continue
            known_entities.add(identity)
            new_entities.append(
                ZontRadioTriggeredBinarySensor(
                    entry,
                    obj.object_id,
                    obj.subtype,
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        entry.runtime_data.coordinator.async_add_listener(async_add_object_entities)
    )
    async_add_object_entities()


class ZontConnectedBinarySensor(ZontEntityMixin, BinarySensorEntity):
    """Represent the ZONT WebSocket connection state."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the sensor."""
        self._entry = entry
        self._attr_is_on = entry.runtime_data.client.is_connected
        self._set_zont_identity(entry, "connected")

    async def async_added_to_hass(self) -> None:
        """Subscribe to connection changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                connection_signal(self._entry.entry_id),
                self._async_connection_changed,
            )
        )

    @callback
    def _async_connection_changed(self, connected: bool) -> None:
        """Handle a WebSocket connection state change."""
        self._attr_is_on = connected
        self.async_write_ha_state()


class ZontCloudConnectedBinarySensor(ZontCoordinatorEntity, BinarySensorEntity):
    """Represent the controller connection to the ZONT cloud."""

    _attr_translation_key = "cloud_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry[ZontRuntimeData]) -> None:
        """Initialize the cloud connection sensor."""
        super().__init__(entry, "cloud_connected")

    @property
    def available(self) -> bool:
        """Return whether server status has been obtained."""
        return super().available and self.controller_data.server_status is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the controller is connected to the ZONT cloud."""
        status = self.controller_data.server_status
        return status.cloud_connected if status is not None else None


class ZontHeatingCircuitBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent one binary state of a heating circuit."""

    entity_description: ZontHeatingCircuitBinarySensorEntityDescription

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        description: ZontHeatingCircuitBinarySensorEntityDescription,
    ) -> None:
        """Initialize a heating-circuit binary sensor."""
        self.entity_description = description
        super().__init__(entry, object_id, description.key, description.key)

    @property
    def available(self) -> bool:
        """Return whether the source currently provides this binary state."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the described consumer heating-circuit state."""
        obj = self.object_data
        if not isinstance(obj, ZontHeatingCircuitData):
            return None
        return self.entity_description.value_fn(
            obj,
            self.coordinator.data.heating_controls.get(self._object_id),
            self.coordinator.data.heating_states.get(self._object_id),
        )


class ZontPumpRunningBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the observed running state of one pump."""

    _attr_name = None
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a pump running-state sensor."""
        super().__init__(entry, object_id, "running", None)

    @property
    def available(self) -> bool:
        """Return whether the pump currently provides its running state."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the pump is physically running."""
        obj = self.object_data
        return obj.running if isinstance(obj, ZontPumpData) else None


class ZontMixerBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent one read-only mixer diagnostic flag."""

    entity_description: ZontMixerBinarySensorEntityDescription

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        description: ZontMixerBinarySensorEntityDescription,
    ) -> None:
        """Initialize a mixer diagnostic binary sensor."""
        self.entity_description = description
        super().__init__(entry, object_id, description.key, description.key)

    @property
    def available(self) -> bool:
        """Return whether internal mixer flags have been read."""
        return super().available and self._internal_state is not None

    @property
    def is_on(self) -> bool | None:
        """Return the described mixer diagnostic flag."""
        state = self._internal_state
        return self.entity_description.value_fn(state) if state is not None else None

    @property
    def _internal_state(self) -> ZontMixerInternalState | None:
        """Return the current internal state of this mixer."""
        obj = self.object_data
        if not isinstance(obj, ZontMixerData):
            return None
        return self.coordinator.data.mixer_states.get(self._object_id)


class ZontRelayFailedBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the internal failure flag of one relay."""

    _attr_translation_key = "relay_failed"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a relay failure sensor."""
        super().__init__(entry, object_id, "failed", "failed")

    @property
    def available(self) -> bool:
        """Return whether internal relay flags have been read."""
        return super().available and self._internal_state is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the relay reports a failure."""
        state = self._internal_state
        return state.has_failed if state is not None else None

    @property
    def _internal_state(self) -> ZontRelayInternalState | None:
        """Return the current internal state of this relay."""
        obj = self.object_data
        if not isinstance(obj, ZontRelayData):
            return None
        return self.coordinator.data.relay_states.get(self._object_id)


class ZontAnalogInputTriggeredBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the trigger flag of one analog input."""

    _attr_translation_key = "analog_triggered"

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        subtype: int,
    ) -> None:
        """Initialize an analog input trigger sensor."""
        self._attr_device_class = ANALOG_TRIGGER_DEVICE_CLASSES.get(
            subtype,
            BinarySensorDeviceClass.PROBLEM,
        )
        super().__init__(entry, object_id, "triggered", "triggered")

    @property
    def available(self) -> bool:
        """Return whether the input currently reports a valid trigger flag."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the current analog input trigger flag."""
        obj = self.object_data
        return obj.triggered if isinstance(obj, ZontAnalogInputData) else None


class ZontRadioTriggeredBinarySensor(
    ZontObjectCoordinatorEntity,
    BinarySensorEntity,
):
    """Represent the trigger flag of one radio sensor."""

    _attr_translation_key = "radio_triggered"

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
        subtype: int,
    ) -> None:
        """Initialize a radio sensor trigger entity."""
        self._attr_device_class = RADIO_TRIGGER_DEVICE_CLASSES[subtype]
        super().__init__(entry, object_id, "triggered", "triggered")

    @property
    def available(self) -> bool:
        """Return whether the radio sensor reports a valid trigger flag."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the current radio sensor trigger flag."""
        obj = self.object_data
        return obj.triggered if isinstance(obj, ZontRadioSensorData) else None
