"""Constants for the ZONT WebSocket integration."""

from homeassistant.const import Platform

DOMAIN = "zont_ws"

CONFIG_ENTRY_VERSION = 3
CONF_CONTROLLER = "controller"
CONF_AUTO_TITLE = "auto_title"
CONF_HEATING_OFF_MODE_ID = "heating_off_mode_id"
CONF_DHW_ON_TEMPERATURE = "dhw_on_temperature"

DHW_MIN_TARGET_TEMPERATURE = 5.0
DHW_MAX_TARGET_TEMPERATURE = 75.0
DHW_DEFAULT_ON_TEMPERATURE = 60.0

SERVICE_SEND_COMMAND = "send_command"
SERVICE_SEND_BULK = "send_bulk"

EVENT_MESSAGE = f"{DOMAIN}_event"

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.WATER_HEATER,
)

CONNECTION_TIMEOUT = 5.0
COMMAND_TIMEOUT = 10.0
CONTROLLER_INFO_TIMEOUT = 3.0
WS_HEARTBEAT = 30.0
RECONNECT_DELAYS: tuple[float, ...] = (1, 2, 4, 8, 16, 30)


def connection_signal(entry_id: str) -> str:
    """Return the dispatcher signal for a config entry connection state."""
    return f"{DOMAIN}_{entry_id}_connection"
