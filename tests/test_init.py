"""Tests for ZONT integration setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.zont_ws import (
    _async_sync_object_devices,
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.zont_ws.client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontProtocolError,
    ZontWsClient,
)
from custom_components.zont_ws.const import (
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    connection_signal,
)
from custom_components.zont_ws.controller import ZontControllerInfo
from custom_components.zont_ws.coordinator import (
    ZontControllerData,
    ZontData,
    ZontDataUpdateCoordinator,
    ZontRuntimeData,
)
from custom_components.zont_ws.objects import (
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontDigitalTemperatureSensorData,
    ZontNtcTemperatureSensorData,
    ZontRadioSensorData,
    immutable_objects,
)
from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
}
SERIAL_NUMBER = "ABCDEF123456"
CONTROLLER_INFO = ZontControllerInfo(
    serial_number=SERIAL_NUMBER,
    model="H1V02 PRO",
    board_model="700",
    firmware_version="625",
)


async def test_legacy_entry_requires_adding_controller_again(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_URL: "ws://controller.local/ws",
            CONF_USERNAME: "legacy-user",
            CONF_PASSWORD: "legacy-password",
        },
    )
    entry.add_to_hass(hass)

    assert not await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data == {
        CONF_URL: "ws://controller.local/ws",
        CONF_USERNAME: "legacy-user",
        CONF_PASSWORD: "legacy-password",
    }


async def test_setup_stores_runtime_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(ZontDataUpdateCoordinator, "async_start") as start_coordinator,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)

    assert isinstance(entry.runtime_data, ZontRuntimeData)
    assert isinstance(entry.runtime_data.client, ZontWsClient)
    assert isinstance(entry.runtime_data.coordinator, ZontDataUpdateCoordinator)
    assert entry.runtime_data.client._url == "ws://192.0.2.10/ws"
    start_coordinator.assert_called_once_with()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL_NUMBER)})
    assert device is not None
    assert device.name == "ZONT H1V02 PRO"
    assert device.manufacturer == "ZONT"
    assert device.model == "H1V02 PRO"
    assert device.model_id == "700"
    assert device.sw_version == "625"
    assert device.serial_number == SERIAL_NUMBER
    assert device.configuration_url == "http://192.0.2.10"
    await entry.runtime_data.coordinator.async_shutdown()


async def test_new_entities_use_device_prefix_and_stable_suffixes(
    hass: HomeAssistant,
) -> None:
    """Generate descriptive entity IDs without renaming registered entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(ZontDataUpdateCoordinator, "async_start"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    expected_ids = {
        (
            "binary_sensor",
            f"{SERIAL_NUMBER}_connected",
        ): "binary_sensor.zont_h1v02_pro_connected",
        (
            "binary_sensor",
            f"{SERIAL_NUMBER}_cloud_connected",
        ): "binary_sensor.zont_h1v02_pro_cloud_connected",
        ("button", f"{SERIAL_NUMBER}_restart"): "button.zont_h1v02_pro_restart",
        (
            "sensor",
            f"{SERIAL_NUMBER}_connection_channel",
        ): "sensor.zont_h1v02_pro_connection_channel",
        (
            "sensor",
            f"{SERIAL_NUMBER}_supply_voltage",
        ): "sensor.zont_h1v02_pro_supply_voltage",
    }
    for (platform, unique_id), entity_id in expected_ids.items():
        assert registry.async_get_entity_id(platform, DOMAIN, unique_id) == entity_id

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_digital_bus_adapter_is_registered_as_child_device(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    controller = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        name="ZONT H1V02 PRO",
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects({4097: ZontDigitalBusAdapterData(4097, 6, "Navien")}),
    )
    entry.runtime_data = ZontRuntimeData(MagicMock(spec=ZontWsClient), coordinator)

    _async_sync_object_devices(hass, entry, controller.id)

    adapter = registry.async_get_device(
        identifiers={(DOMAIN, f"{SERIAL_NUMBER}:object:4097")}
    )
    assert adapter is not None
    assert adapter.name == "Navien"
    assert adapter.manufacturer == "ZONT"
    assert adapter.model == "Адаптер цифровой шины"
    assert adapter.via_device_id == controller.id

    registry.async_update_device(adapter.id, name_by_user="Мой котёл")
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects(
            {4097: ZontDigitalBusAdapterData(4097, 6, "Новый Navien")}
        ),
    )
    _async_sync_object_devices(hass, entry, controller.id)

    adapter = registry.async_get(adapter.id)
    assert adapter is not None
    assert adapter.name == "Новый Navien"
    assert adapter.name_by_user == "Мой котёл"


async def test_analog_input_is_registered_as_typed_child_device(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    controller = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        name="ZONT H1V02 PRO",
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects(
            {
                20550: ZontAnalogInputData(
                    object_id=20550,
                    object_type=0,
                    name="Влажность",
                    subtype=17,
                    value=45,
                    unit_code=7,
                    triggered=False,
                )
            }
        ),
    )
    entry.runtime_data = ZontRuntimeData(MagicMock(spec=ZontWsClient), coordinator)

    _async_sync_object_devices(hass, entry, controller.id)

    analog_input = registry.async_get_device(
        identifiers={(DOMAIN, f"{SERIAL_NUMBER}:object:20550")}
    )
    assert analog_input is not None
    assert analog_input.name == "Влажность"
    assert analog_input.manufacturer is None
    assert analog_input.model == "Датчик влажности"
    assert analog_input.via_device_id == controller.id


async def test_unknown_analog_subtype_has_fallback_device_model(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    controller = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        name="ZONT H1V02 PRO",
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects(
            {
                20550: ZontAnalogInputData(
                    object_id=20550,
                    object_type=0,
                    name="Будущий вход",
                    subtype=22,
                )
            }
        ),
    )
    entry.runtime_data = ZontRuntimeData(MagicMock(spec=ZontWsClient), coordinator)

    _async_sync_object_devices(hass, entry, controller.id)

    analog_input = registry.async_get_device(
        identifiers={(DOMAIN, f"{SERIAL_NUMBER}:object:20550")}
    )
    assert analog_input is not None
    assert analog_input.model == "Аналоговый вход (подтип 22)"


async def test_discovered_adapter_creates_prefixed_entities(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)

    def start_with_adapter(coordinator: ZontDataUpdateCoordinator) -> None:
        coordinator.data = ZontData(
            controller=ZontControllerData(info=CONTROLLER_INFO),
            objects=immutable_objects(
                {
                    4097: ZontDigitalBusAdapterData(
                        object_id=4097,
                        object_type=6,
                        name="Navien",
                        flow_temperature=35,
                        state=ZontDigitalBusState.OFF,
                        error_code=0,
                    )
                }
            ),
        )
        coordinator.async_update_listeners()

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(
            ZontDataUpdateCoordinator,
            "async_start",
            new=start_with_adapter,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_4097_flow_temperature",
        )
        == "sensor.navien_flow_temperature"
    )
    assert (
        registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_4097_state",
        )
        == "sensor.navien_state"
    )
    assert (
        registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_4097_error_code",
        )
        == "sensor.navien_error_code"
    )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_temperature_sensor_is_registered_as_child_device(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    controller = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        name="ZONT H1V02 PRO",
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects(
            {
                4107: ZontDigitalTemperatureSensorData(
                    4107,
                    1,
                    "Т Спальня",
                    temperature=25.5,
                )
            }
        ),
    )
    entry.runtime_data = ZontRuntimeData(MagicMock(spec=ZontWsClient), coordinator)

    _async_sync_object_devices(hass, entry, controller.id)

    sensor = registry.async_get_device(
        identifiers={(DOMAIN, f"{SERIAL_NUMBER}:object:4107")}
    )
    assert sensor is not None
    assert sensor.name == "Т Спальня"
    assert sensor.manufacturer is None
    assert sensor.model == "Цифровой датчик температуры"
    assert sensor.via_device_id == controller.id


async def test_ntc_sensor_is_registered_as_child_device(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    controller = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        name="ZONT H1V02 PRO",
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects(
            {
                20487: ZontNtcTemperatureSensorData(
                    object_id=20487,
                    object_type=27,
                    name="Температура котла",
                    temperature=45.6,
                )
            }
        ),
    )
    entry.runtime_data = ZontRuntimeData(MagicMock(spec=ZontWsClient), coordinator)

    _async_sync_object_devices(hass, entry, controller.id)

    sensor = registry.async_get_device(
        identifiers={(DOMAIN, f"{SERIAL_NUMBER}:object:20487")}
    )
    assert sensor is not None
    assert sensor.name == "Температура котла"
    assert sensor.manufacturer is None
    assert sensor.model == "NTC-термодатчик"
    assert sensor.via_device_id == controller.id


async def test_supported_radio_sensor_is_registered_as_typed_child_device(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    controller = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        name="ZONT H1V02 PRO",
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects(
            {
                12001: ZontRadioSensorData(
                    object_id=12001,
                    object_type=8,
                    name="Гостиная",
                    subtype=18,
                    temperature=23.4,
                    humidity=48,
                )
            }
        ),
    )
    entry.runtime_data = ZontRuntimeData(MagicMock(spec=ZontWsClient), coordinator)

    _async_sync_object_devices(hass, entry, controller.id)

    sensor = registry.async_get_device(
        identifiers={(DOMAIN, f"{SERIAL_NUMBER}:object:12001")}
    )
    assert sensor is not None
    assert sensor.name == "Гостиная"
    assert sensor.manufacturer is None
    assert sensor.model == "Радиодатчик температуры и влажности"
    assert sensor.via_device_id == controller.id


async def test_unsupported_radio_subtype_is_not_registered(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    controller = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        name="ZONT H1V02 PRO",
    )
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.data = ZontData(
        controller=ZontControllerData(info=CONTROLLER_INFO),
        objects=immutable_objects(
            {
                12002: ZontRadioSensorData(
                    object_id=12002,
                    object_type=8,
                    name="Радиопанель",
                    subtype=23,
                )
            }
        ),
    )
    entry.runtime_data = ZontRuntimeData(MagicMock(spec=ZontWsClient), coordinator)

    _async_sync_object_devices(hass, entry, controller.id)

    assert (
        registry.async_get_device(
            identifiers={(DOMAIN, f"{SERIAL_NUMBER}:object:12002")}
        )
        is None
    )


async def test_discovered_temperature_sensor_creates_prefixed_entity(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)

    def start_with_temperature_sensor(
        coordinator: ZontDataUpdateCoordinator,
    ) -> None:
        coordinator.data = ZontData(
            controller=ZontControllerData(info=CONTROLLER_INFO),
            objects=immutable_objects(
                {
                    4107: ZontDigitalTemperatureSensorData(
                        object_id=4107,
                        object_type=1,
                        name="Т Спальня",
                        temperature=25.5,
                    )
                }
            ),
        )
        coordinator.async_update_listeners()

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(
            ZontDataUpdateCoordinator,
            "async_start",
            new=start_with_temperature_sensor,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_4107_temperature",
        )
        == "sensor.t_spalnia_temperature"
    )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_discovered_ntc_sensor_creates_prefixed_entity(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)

    def start_with_ntc_sensor(coordinator: ZontDataUpdateCoordinator) -> None:
        coordinator.data = ZontData(
            controller=ZontControllerData(info=CONTROLLER_INFO),
            objects=immutable_objects(
                {
                    20487: ZontNtcTemperatureSensorData(
                        object_id=20487,
                        object_type=27,
                        name="Температура котла",
                        temperature=45.6,
                    )
                }
            ),
        )
        coordinator.async_update_listeners()

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(
            ZontDataUpdateCoordinator,
            "async_start",
            new=start_with_ntc_sensor,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_20487_temperature",
        )
        == "sensor.temperatura_kotla_temperature"
    )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_discovered_radio_sensor_creates_prefixed_entities(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)

    def start_with_radio_sensor(coordinator: ZontDataUpdateCoordinator) -> None:
        coordinator.data = ZontData(
            controller=ZontControllerData(info=CONTROLLER_INFO),
            objects=immutable_objects(
                {
                    12001: ZontRadioSensorData(
                        object_id=12001,
                        object_type=8,
                        name="Гостиная",
                        subtype=18,
                        temperature=23.4,
                        humidity=48,
                        battery_voltage=2.91,
                        signal_strength_raw=86,
                    )
                }
            ),
        )
        coordinator.async_update_listeners()

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(
            ZontDataUpdateCoordinator,
            "async_start",
            new=start_with_radio_sensor,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    for suffix in (
        "temperature",
        "humidity",
        "battery_voltage",
        "signal_strength",
    ):
        assert (
            registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                f"{SERIAL_NUMBER}_12001_{suffix}",
            )
            == f"sensor.gostinaia_{suffix}"
        )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_discovered_analog_input_creates_prefixed_entities(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)

    def start_with_analog_input(coordinator: ZontDataUpdateCoordinator) -> None:
        coordinator.data = ZontData(
            controller=ZontControllerData(info=CONTROLLER_INFO),
            objects=immutable_objects(
                {
                    20550: ZontAnalogInputData(
                        object_id=20550,
                        object_type=0,
                        name="Контроль напряжения питания",
                        subtype=0,
                        value=12.2,
                        unit_code=0,
                        triggered=False,
                    )
                }
            ),
        )
        coordinator.async_update_listeners()

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(
            ZontDataUpdateCoordinator,
            "async_start",
            new=start_with_analog_input,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_20550_value",
        )
        == "sensor.kontrol_napriazheniia_pitaniia_value"
    )
    assert (
        registry.async_get_entity_id(
            "binary_sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_20550_triggered",
        )
        == "binary_sensor.kontrol_napriazheniia_pitaniia_triggered"
    )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_registered_entity_id_is_preserved(hass: HomeAssistant) -> None:
    """Do not rename an entity already stored in the entity registry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: "ZONT H1V02 PRO (192.0.2.10)",
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registered = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SERIAL_NUMBER}_supply_voltage",
        config_entry=entry,
        suggested_object_id="legacy_voltage",
    )

    with (
        patch.object(ZontWsClient, "async_start", new=AsyncMock()),
        patch.object(ZontDataUpdateCoordinator, "async_start"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{SERIAL_NUMBER}_supply_voltage",
        )
        == registered.entity_id
        == "sensor.legacy_voltage"
    )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_refreshes_controller_information(hass: HomeAssistant) -> None:
    old_info = ZontControllerInfo(
        serial_number=SERIAL_NUMBER,
        model="H1V02 PRO",
        board_model="700",
        firmware_version="624",
    )
    old_title = "ZONT H1V02 PRO (192.0.2.10)"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=old_title,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: old_info.as_dict(),
            CONF_AUTO_TITLE: old_title,
        },
    )
    entry.add_to_hass(hass)

    async def async_start(client: ZontWsClient, entry: MockConfigEntry) -> None:
        client._is_connected = True

    with (
        patch.object(ZontWsClient, "async_start", new=async_start),
        patch(
            "custom_components.zont_ws.coordinator.async_refresh_controller_info",
            new=AsyncMock(return_value=CONTROLLER_INFO),
        ) as refresh,
        patch.object(
            ZontWsClient,
            "async_send_system_command",
            new=AsyncMock(side_effect=["#S224:1 0 1 0", "#S6:123 0"]),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)
        await hass.async_block_till_done()

    refresh.assert_awaited_once_with(entry.runtime_data.client, SERIAL_NUMBER)
    assert entry.data[CONF_CONTROLLER] == CONTROLLER_INFO.as_dict()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL_NUMBER)})
    assert device is not None
    assert device.sw_version == "625"
    await entry.runtime_data.coordinator.async_shutdown()


async def test_failed_information_refresh_is_disabled_until_restart(
    hass: HomeAssistant,
) -> None:
    title = "ZONT H1V02 PRO (192.0.2.10)"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=SERIAL_NUMBER,
        data={
            **ENTRY_DATA,
            CONF_CONTROLLER: CONTROLLER_INFO.as_dict(),
            CONF_AUTO_TITLE: title,
        },
    )
    entry.add_to_hass(hass)

    async def async_start(client: ZontWsClient, entry: MockConfigEntry) -> None:
        client._is_connected = True

    with (
        patch.object(ZontWsClient, "async_start", new=async_start),
        patch(
            "custom_components.zont_ws.coordinator.async_refresh_controller_info",
            new=AsyncMock(side_effect=ZontProtocolError),
        ) as refresh,
        patch.object(
            ZontWsClient,
            "async_send_system_command",
            new=AsyncMock(
                side_effect=[
                    "#S224:1 0 1 0",
                    "#S6:123 0",
                    "#S224:1 0 1 0",
                    "#S6:123 0",
                ]
            ),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)
        await hass.async_block_till_done()
        async_dispatcher_send(hass, connection_signal(entry.entry_id), True)
        await hass.async_block_till_done()

    refresh.assert_awaited_once_with(entry.runtime_data.client, SERIAL_NUMBER)
    await entry.runtime_data.coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("client_error", "expected_error"),
    [
        (ZontConnectionError(), ConfigEntryNotReady),
        (ZontAuthenticationError(), ConfigEntryAuthFailed),
    ],
)
async def test_setup_maps_client_errors(
    hass: HomeAssistant,
    client_error: Exception,
    expected_error: type[Exception],
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)

    with (
        patch.object(
            ZontWsClient,
            "async_start",
            new=AsyncMock(side_effect=client_error),
        ),
        pytest.raises(expected_error),
    ):
        await async_setup_entry(hass, entry)


async def test_unload_stops_client(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    client = MagicMock(spec=ZontWsClient)
    client.async_stop = AsyncMock()
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.async_shutdown = AsyncMock()
    entry.runtime_data = ZontRuntimeData(client, coordinator)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, entry)

    client.async_stop.assert_awaited_once()
    coordinator.async_shutdown.assert_awaited_once()
