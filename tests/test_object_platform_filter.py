"""Tests that every object-backed platform honors import settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from custom_components.zont_ws.binary_sensor import async_setup_entry as setup_binary
from custom_components.zont_ws.climate import async_setup_entry as setup_climate
from custom_components.zont_ws.const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_IMPORTED_OBJECT_IDS,
    DOMAIN,
)
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
)
from custom_components.zont_ws.objects import (
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontPumpData,
    ZontRelayData,
    immutable_objects,
)
from custom_components.zont_ws.runtime import ZontRuntimeData
from custom_components.zont_ws.sensor import async_setup_entry as setup_sensor
from custom_components.zont_ws.switch import async_setup_entry as setup_switch
from custom_components.zont_ws.water_heater import (
    async_setup_entry as setup_water_heater,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

type SetupPlatform = Callable[..., Awaitable[None]]


@pytest.mark.parametrize(
    ("setup_platform", "obj", "controller_entity_calls"),
    [
        (
            setup_sensor,
            ZontDigitalTemperatureSensorData(1001, 1, "Температура"),
            1,
        ),
        (setup_binary, ZontPumpData(1002, 17, "Насос"), 1),
        (setup_climate, ZontHeatingCircuitData(1003, 16, "ТП", subtype=3), 0),
        (
            setup_water_heater,
            ZontHeatingCircuitData(1004, 16, "ГВС", subtype=1),
            0,
        ),
        (setup_switch, ZontRelayData(1005, 14, "Реле"), 0),
    ],
)
async def test_excluded_object_is_not_added_by_platform(
    hass: HomeAssistant,
    setup_platform: SetupPlatform,
    obj: object,
    controller_entity_calls: int,
) -> None:
    object_id = obj.object_id  # type: ignore[attr-defined]
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
        options={
            CONF_IMPORTED_OBJECT_IDS: [],
            CONF_EXCLUDED_OBJECT_IDS: [object_id],
            CONF_AUTO_IMPORT_NEW_OBJECTS: True,
        },
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects({object_id: obj}),  # type: ignore[dict-item]
    )
    coordinator.async_add_listener = MagicMock()
    entry.runtime_data = ZontRuntimeData(MagicMock(), coordinator)
    async_add_entities = MagicMock()

    await setup_platform(hass, entry, async_add_entities)

    assert async_add_entities.call_count == controller_entity_calls


async def test_sensor_import_changes_reconcile_only_selected_object(
    hass: HomeAssistant,
) -> None:
    """Changing import options must not recreate unaffected entities."""
    first_id = 1001
    second_id = 1002
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCDEF123456",
        data={},
        options={
            CONF_IMPORTED_OBJECT_IDS: [first_id, second_id],
            CONF_EXCLUDED_OBJECT_IDS: [],
            CONF_AUTO_IMPORT_NEW_OBJECTS: False,
        },
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                first_id: ZontDigitalTemperatureSensorData(
                    first_id, 1, "Температура 1"
                ),
                second_id: ZontDigitalTemperatureSensorData(
                    second_id, 1, "Температура 2"
                ),
            }
        ),
    )
    coordinator.async_add_listener = MagicMock()
    entry.runtime_data = ZontRuntimeData(MagicMock(), coordinator)
    async_add_entities = MagicMock()

    await setup_sensor(hass, entry, async_add_entities)

    initially_added = async_add_entities.call_args_list[1].args[0]
    first_entity = next(
        entity for entity in initially_added if f"_{first_id}_" in entity.unique_id
    )
    second_entity = next(
        entity for entity in initially_added if f"_{second_id}_" in entity.unique_id
    )

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_IMPORTED_OBJECT_IDS: [second_id],
            CONF_EXCLUDED_OBJECT_IDS: [first_id],
            CONF_AUTO_IMPORT_NEW_OBJECTS: False,
        },
    )
    await entry.runtime_data.object_entities.async_reconcile()
    assert async_add_entities.call_count == 2

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_IMPORTED_OBJECT_IDS: [first_id, second_id],
            CONF_EXCLUDED_OBJECT_IDS: [],
            CONF_AUTO_IMPORT_NEW_OBJECTS: False,
        },
    )
    await entry.runtime_data.object_entities.async_reconcile()

    assert async_add_entities.call_count == 3
    reimported = async_add_entities.call_args.args[0]
    assert len(reimported) == 1
    assert reimported[0].unique_id == first_entity.unique_id
    assert reimported[0] is not first_entity
    assert second_entity not in reimported
