"""Shared rules for exposing ZONT objects in Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_IMPORTED_OBJECT_IDS,
)
from .export import export_target_ids
from .object_descriptions import (
    SUPPORTED_RADIO_SENSOR_SUBTYPES,
    analog_input_model,
    heating_circuit_model,
    radio_sensor_model,
)
from .protocol.objects import (
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontNtcTemperatureSensorData,
    ZontObject,
    ZontPumpData,
    ZontRadioSensorData,
    ZontRelayData,
)


@dataclass(frozen=True, slots=True)
class ZontImportableObject:
    """Describe one ZONT object exposed as a Home Assistant device."""

    object_id: int
    name: str
    device_type: str
    manufacturer: str | None
    model: str

    @property
    def selector_label(self) -> str:
        """Return the Russian label used by config and options flows."""
        return f"{self.device_type} - {self.name} (ID {self.object_id})"

    @property
    def sort_key(self) -> tuple[str, str, int]:
        """Return a stable, human-friendly selector order."""
        return (self.device_type.casefold(), self.name.casefold(), self.object_id)


@dataclass(frozen=True, slots=True)
class ZontObjectImportConfiguration:
    """Normalized object import settings."""

    imported_ids: frozenset[int]
    excluded_ids: frozenset[int]
    auto_import_new: bool
    exported_ids: frozenset[int] = frozenset()
    legacy_import_all: bool = False

    def imports(self, object_id: int) -> bool:
        """Return whether one object should be exposed in Home Assistant."""
        if object_id in self.exported_ids:
            return False
        if self.legacy_import_all:
            return True
        if object_id in self.imported_ids:
            return True
        if object_id in self.excluded_ids:
            return False
        return self.auto_import_new


def importable_object_descriptor(obj: ZontObject) -> ZontImportableObject | None:
    """Return registry and selector metadata for a supported public object."""
    manufacturer: str | None = None
    if isinstance(obj, ZontAnalogInputData):
        model = analog_input_model(obj.subtype)
    elif isinstance(obj, ZontDigitalBusAdapterData):
        manufacturer = "ZONT"
        model = "Адаптер цифровой шины"
    elif isinstance(obj, ZontDigitalTemperatureSensorData):
        model = "Цифровой датчик температуры"
    elif isinstance(obj, ZontNtcTemperatureSensorData):
        model = "NTC-термодатчик"
    elif isinstance(obj, ZontHeatingCircuitData) and obj.subtype in (1, 3):
        model = heating_circuit_model(obj.subtype)
    elif isinstance(obj, ZontPumpData):
        model = "Насос"
    elif isinstance(obj, ZontMixerData):
        model = "Смеситель"
    elif isinstance(obj, ZontRelayData):
        model = "Реле"
    elif (
        isinstance(obj, ZontRadioSensorData)
        and obj.subtype in SUPPORTED_RADIO_SENSOR_SUBTYPES
    ):
        model = radio_sensor_model(obj.subtype)
    else:
        return None
    return ZontImportableObject(
        object_id=obj.object_id,
        name=obj.name,
        device_type=model,
        manufacturer=manufacturer,
        model=model,
    )


def importable_object_descriptors(
    objects: Mapping[int, ZontObject],
) -> tuple[ZontImportableObject, ...]:
    """Return sorted descriptions of all public objects in a snapshot."""
    descriptors = (
        descriptor
        for obj in objects.values()
        if (descriptor := importable_object_descriptor(obj)) is not None
    )
    return tuple(sorted(descriptors, key=lambda descriptor: descriptor.sort_key))


def object_import_configuration(
    options: Mapping[str, Any],
) -> ZontObjectImportConfiguration:
    """Normalize stored options, preserving safe legacy import-all behavior."""
    exported_ids = export_target_ids(options)
    if (
        CONF_IMPORTED_OBJECT_IDS not in options
        and CONF_EXCLUDED_OBJECT_IDS not in options
    ):
        return ZontObjectImportConfiguration(
            imported_ids=frozenset(),
            excluded_ids=frozenset(),
            auto_import_new=True,
            exported_ids=exported_ids,
            legacy_import_all=True,
        )

    imported_ids = _valid_object_id_set(options.get(CONF_IMPORTED_OBJECT_IDS, []))
    excluded_ids = _valid_object_id_set(options.get(CONF_EXCLUDED_OBJECT_IDS, []))
    auto_import_new = options.get(CONF_AUTO_IMPORT_NEW_OBJECTS, True)
    if (
        imported_ids is None
        or excluded_ids is None
        or type(auto_import_new) is not bool
    ):
        return ZontObjectImportConfiguration(
            imported_ids=frozenset(),
            excluded_ids=frozenset(),
            auto_import_new=True,
            exported_ids=exported_ids,
            legacy_import_all=True,
        )
    return ZontObjectImportConfiguration(
        imported_ids=imported_ids,
        excluded_ids=excluded_ids - imported_ids,
        auto_import_new=auto_import_new,
        exported_ids=exported_ids,
    )


def _valid_object_id_set(value: Any) -> frozenset[int] | None:
    """Return valid non-negative object IDs or None for malformed storage."""
    if not isinstance(value, list | tuple) or any(
        type(object_id) is not int or object_id < 0 for object_id in value
    ):
        return None
    return frozenset(value)
