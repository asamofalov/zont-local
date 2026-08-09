"""ZONT WebSocket integration."""

from __future__ import annotations

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    ZontWsClient,
)
from .const import (
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .controller import (
    ZontControllerInfo,
    controller_configuration_url,
    controller_device_name,
    controller_entry_title,
    controller_websocket_url,
)
from .coordinator import ZontDataUpdateCoordinator, ZontRuntimeData
from .objects import (
    SUPPORTED_RADIO_SENSOR_SUBTYPES,
    ZontAnalogInputData,
    ZontDigitalBusAdapterData,
    ZontDigitalTemperatureSensorData,
    ZontHeatingCircuitData,
    ZontMixerData,
    ZontNtcTemperatureSensorData,
    ZontPumpData,
    ZontRadioSensorData,
    ZontRelayData,
    analog_input_model,
    heating_circuit_model,
    object_device_identifier,
    radio_sensor_model,
)
from .services import async_setup_services

type ZontConfigEntry = ConfigEntry[ZontRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level service actions."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Reject entries from versions that require adding the controller again."""
    return entry.version == CONFIG_ENTRY_VERSION


async def async_setup_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Set up ZONT from a config entry."""
    controller_info = ZontControllerInfo.from_mapping(entry.data.get(CONF_CONTROLLER))
    if controller_info is None and entry.unique_id is not None:
        controller_info = ZontControllerInfo.from_mapping(
            {"serial_number": entry.unique_id}
        )
    controller_identifier = entry.unique_id or entry.entry_id
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, controller_identifier)},
        name=controller_device_name(controller_info),
        manufacturer="ZONT",
        model=controller_info.model if controller_info is not None else None,
        model_id=(controller_info.board_model if controller_info is not None else None),
        sw_version=(
            controller_info.firmware_version if controller_info is not None else None
        ),
        serial_number=(
            controller_info.serial_number if controller_info is not None else None
        ),
        configuration_url=controller_configuration_url(entry.data[CONF_HOST]),
    )

    client = ZontWsClient(
        hass=hass,
        session=async_get_clientsession(hass),
        url=controller_websocket_url(entry.data[CONF_HOST]),
        credentials=ZontCredentials(
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
        ),
        entry_id=entry.entry_id,
        device_id=device.id,
        on_authentication_error=lambda: entry.async_start_reauth(hass),
    )

    try:
        await client.async_start(entry)
    except ZontAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
        ) from err
    except (ZontConnectionError, ZontProtocolError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err

    coordinator = ZontDataUpdateCoordinator(
        hass,
        entry,
        client,
        controller_info,
        lambda info: _async_apply_controller_info(
            hass,
            entry,
            device.id,
            info,
        ),
    )
    entry.runtime_data = ZontRuntimeData(client=client, coordinator=coordinator)
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _async_sync_object_devices(
                hass,
                entry,
                device.id,
            )
        )
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        try:
            await coordinator.async_shutdown()
        finally:
            await client.async_stop()
        raise

    coordinator.async_start()

    return True


@callback
def _async_sync_object_devices(
    hass: HomeAssistant,
    entry: ZontConfigEntry,
    controller_device_id: str,
) -> None:
    """Create or update devices represented by discovered ZONT objects."""
    controller_identifier = entry.unique_id or entry.entry_id
    device_registry = dr.async_get(hass)
    for obj in entry.runtime_data.coordinator.data.objects.values():
        if isinstance(obj, ZontAnalogInputData):
            manufacturer = None
            model = analog_input_model(obj.subtype)
        elif isinstance(obj, ZontDigitalBusAdapterData):
            manufacturer = "ZONT"
            model = "Адаптер цифровой шины"
        elif isinstance(obj, ZontDigitalTemperatureSensorData):
            manufacturer = None
            model = "Цифровой датчик температуры"
        elif isinstance(obj, ZontNtcTemperatureSensorData):
            manufacturer = None
            model = "NTC-термодатчик"
        elif isinstance(obj, ZontHeatingCircuitData) and obj.subtype in (1, 3):
            manufacturer = None
            model = heating_circuit_model(obj.subtype)
        elif isinstance(obj, ZontPumpData):
            manufacturer = None
            model = "Насос"
        elif isinstance(obj, ZontMixerData):
            manufacturer = None
            model = "Смеситель"
        elif isinstance(obj, ZontRelayData):
            manufacturer = None
            model = "Реле"
        elif (
            isinstance(obj, ZontRadioSensorData)
            and obj.subtype in SUPPORTED_RADIO_SENSOR_SUBTYPES
        ):
            manufacturer = None
            model = radio_sensor_model(obj.subtype)
        else:
            continue
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={
                (
                    DOMAIN,
                    object_device_identifier(controller_identifier, obj.object_id),
                )
            },
            name=obj.name,
            manufacturer=manufacturer,
            model=model,
            via_device_id=controller_device_id,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ZontConfigEntry) -> bool:
    """Unload a ZONT config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    runtime_data = entry.runtime_data
    try:
        await runtime_data.coordinator.async_shutdown()
    finally:
        await runtime_data.client.async_stop()
    return True


@callback
def _async_apply_controller_info(
    hass: HomeAssistant,
    entry: ZontConfigEntry,
    device_id: str,
    info: ZontControllerInfo,
) -> None:
    """Persist refreshed controller data and update its device registry entry."""
    title = controller_entry_title(info, entry.data[CONF_HOST])
    previous_auto_title = entry.data.get(CONF_AUTO_TITLE)
    title_is_managed = (
        entry.title == previous_auto_title or entry.title == "ZONT WebSocket"
    )
    data = dict(entry.data)
    data[CONF_CONTROLLER] = info.as_dict()
    data[CONF_AUTO_TITLE] = title
    updated_title = title if title_is_managed else entry.title
    if data != entry.data or updated_title != entry.title:
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            title=updated_title,
        )

    dr.async_get(hass).async_update_device(
        device_id,
        name=controller_device_name(info),
        manufacturer="ZONT",
        model=info.model,
        model_id=info.board_model,
        sw_version=info.firmware_version,
        serial_number=info.serial_number,
        configuration_url=controller_configuration_url(entry.data[CONF_HOST]),
    )
