"""Constants for the ZONT Local integration."""

from homeassistant.const import Platform

DOMAIN = "zont_local"

CONFIG_ENTRY_VERSION = 3
CONF_CONTROLLER = "controller"
CONF_AUTO_TITLE = "auto_title"
CONF_HEATING_OFF_MODE_ID = "heating_off_mode_id"
CONF_DHW_ON_TEMPERATURE = "dhw_on_temperature"
CONF_IMPORTED_OBJECT_IDS = "imported_object_ids"
CONF_EXCLUDED_OBJECT_IDS = "excluded_object_ids"
CONF_AUTO_IMPORT_NEW_OBJECTS = "auto_import_new_objects"
CONF_TEMPERATURE_EXPORTS = "temperature_exports"
CONF_EXPORT_SOURCE = "source"
CONF_EXPORT_TARGET_ID = "target_id"
CONF_EXPORT_TARGET_NAME = "target_name"

DHW_MIN_TARGET_TEMPERATURE = 5.0
DHW_MAX_TARGET_TEMPERATURE = 75.0
DHW_DEFAULT_ON_TEMPERATURE = 60.0

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 120

EXPORT_HEARTBEAT_INTERVAL = 120

SERVICE_SEND_COMMAND = "send_command"
SERVICE_SEND_BULK = "send_bulk"

EVENT_MESSAGE = f"{DOMAIN}_event"

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
)


def connection_signal(entry_id: str) -> str:
    """Return the dispatcher signal for a config entry connection state."""
    return f"{DOMAIN}_{entry_id}_connection"
