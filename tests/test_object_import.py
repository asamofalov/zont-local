"""Tests for selecting which ZONT objects are exposed in Home Assistant."""

from __future__ import annotations

import pytest
from custom_components.zont_local.const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_EXPORT_KIND,
    CONF_EXPORT_SOURCE,
    CONF_EXPORT_TARGET_ID,
    CONF_EXPORT_TARGET_NAME,
    CONF_EXPORTS,
    CONF_IMPORTED_OBJECT_IDS,
)
from custom_components.zont_local.object_import import (
    importable_object_descriptor,
    object_import_configuration,
)
from custom_components.zont_local.protocol.objects import (
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
    ZontSecurityZoneData,
)


@pytest.mark.parametrize(
    ("obj", "device_type"),
    [
        (ZontAnalogInputData(1, 0, "Давление", subtype=1), "Датчик давления 5 бар"),
        (ZontDigitalBusAdapterData(2, 6, "Котёл"), "Адаптер цифровой шины"),
        (
            ZontDigitalTemperatureSensorData(3, 1, "Улица"),
            "Цифровой датчик температуры",
        ),
        (ZontHeatingCircuitData(4, 16, "ГВС", subtype=1), "Контур ГВС"),
        (ZontHeatingCircuitData(5, 16, "ТП", subtype=3), "Контур потребителя"),
        (ZontMixerData(6, 15, "Смеситель"), "Смеситель"),
        (ZontNtcTemperatureSensorData(7, 27, "NTC"), "NTC-термодатчик"),
        (ZontPumpData(8, 17, "Насос"), "Насос"),
        (
            ZontRadioSensorData(9, 8, "Комната", subtype=18),
            "Радиодатчик температуры и влажности",
        ),
        (ZontRelayData(10, 14, "Реле"), "Реле"),
        (ZontSecurityZoneData(11, 2, "Периметр"), "Охранная зона"),
    ],
)
def test_importable_object_descriptor(obj: ZontObject, device_type: str) -> None:
    descriptor = importable_object_descriptor(obj)

    assert descriptor is not None
    assert descriptor.device_type == device_type
    assert descriptor.selector_label == (
        f"{device_type} - {obj.name} (ID {obj.object_id})"
    )


@pytest.mark.parametrize(
    "obj",
    [
        ZontHeatingCircuitData(1, 16, "Котёл", subtype=0),
        ZontHeatingCircuitData(2, 16, "Охлаждение", subtype=2),
        ZontRadioSensorData(3, 8, "Розетка", subtype=17),
    ],
)
def test_unsupported_public_objects_have_no_descriptor(obj: ZontObject) -> None:
    assert importable_object_descriptor(obj) is None


def test_legacy_options_import_every_object() -> None:
    configuration = object_import_configuration({})

    assert configuration.legacy_import_all
    assert configuration.imports(1)
    assert configuration.imports(99999)


def test_explicit_selection_distinguishes_new_and_excluded_objects() -> None:
    options = {
        CONF_IMPORTED_OBJECT_IDS: [1],
        CONF_EXCLUDED_OBJECT_IDS: [2],
        CONF_AUTO_IMPORT_NEW_OBJECTS: True,
    }

    configuration = object_import_configuration(options)

    assert configuration.imports(1)
    assert not configuration.imports(2)
    assert configuration.imports(3)


def test_auto_import_can_be_disabled() -> None:
    options = {
        CONF_IMPORTED_OBJECT_IDS: [1],
        CONF_EXCLUDED_OBJECT_IDS: [],
        CONF_AUTO_IMPORT_NEW_OBJECTS: False,
    }

    configuration = object_import_configuration(options)

    assert configuration.imports(1)
    assert not configuration.imports(2)


def test_export_target_is_never_imported_as_a_zont_device() -> None:
    options = {
        CONF_IMPORTED_OBJECT_IDS: [4110],
        CONF_EXCLUDED_OBJECT_IDS: [],
        CONF_AUTO_IMPORT_NEW_OBJECTS: True,
        CONF_EXPORTS: [
            {
                CONF_EXPORT_KIND: "temperature",
                CONF_EXPORT_SOURCE: "sensor.office_temperature",
                CONF_EXPORT_TARGET_ID: 4110,
                CONF_EXPORT_TARGET_NAME: "Т Кабинет",
            }
        ],
    }

    configuration = object_import_configuration(options)

    assert configuration.exported_ids == frozenset({4110})
    assert not configuration.imports(4110)
    assert configuration.imports(4111)


@pytest.mark.parametrize(
    "options",
    [
        {CONF_IMPORTED_OBJECT_IDS: [True]},
        {CONF_EXCLUDED_OBJECT_IDS: ["2"]},
        {
            CONF_IMPORTED_OBJECT_IDS: [],
            CONF_AUTO_IMPORT_NEW_OBJECTS: "yes",
        },
    ],
)
def test_malformed_selection_falls_back_to_safe_import_all(
    options: dict[str, object],
) -> None:
    configuration = object_import_configuration(options)

    assert configuration.legacy_import_all
    assert configuration.imports(123)
