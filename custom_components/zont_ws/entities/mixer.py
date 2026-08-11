"""Read-only entities for ZONT mixers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..entity import ZontObjectCoordinatorEntity
from ..protocol.mixer import ZontMixerInternalState
from ..protocol.objects import ZontMixerData, ZontMixerDirection
from ..runtime import ZontRuntimeData

MIXER_STATE_STATES = (
    "idle",
    "opening",
    "closing",
    "fully_open",
    "fully_closed",
)


class ZontMixerStateSensor(ZontObjectCoordinatorEntity, SensorEntity):
    """Represent the read-only movement or end position of one mixer."""

    _attr_name = None
    _attr_translation_key = "mixer_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MIXER_STATE_STATES)

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a mixer state sensor."""
        super().__init__(entry, object_id, "state", None)

    @property
    def available(self) -> bool:
        """Return whether a current, unambiguous mixer state is known."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | None:
        """Return movement first, otherwise the periodically read position."""
        obj = self.object_data
        if not isinstance(obj, ZontMixerData) or obj.direction is None:
            return None
        if obj.direction is ZontMixerDirection.OPENING:
            return "opening"
        if obj.direction is ZontMixerDirection.CLOSING:
            return "closing"

        state = self.coordinator.data.mixer_states.get(self._object_id)
        return _stopped_mixer_state(state)


def _stopped_mixer_state(state: ZontMixerInternalState | None) -> str | None:
    """Resolve one stopped mixer position without guessing contradictions."""
    if state is None:
        return None
    if state.is_fully_open and state.is_fully_closed:
        return None
    if state.is_fully_open:
        return "fully_open"
    if state.is_fully_closed:
        return "fully_closed"
    return "idle"


@dataclass(frozen=True, kw_only=True)
class ZontMixerBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe one diagnostic mixer flag."""

    value_fn: Callable[[ZontMixerInternalState], bool]


MIXER_BINARY_SENSOR_DESCRIPTIONS = (
    ZontMixerBinarySensorEntityDescription(
        key="fault",
        translation_key="mixer_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.has_fault,
    ),
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
