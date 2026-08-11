"""Read-only entity for a ZONT pump."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry

from ..entity import ZontObjectCoordinatorEntity
from ..protocol.objects import ZontPumpData
from ..runtime import ZontRuntimeData


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
