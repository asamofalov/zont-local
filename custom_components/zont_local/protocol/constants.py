"""Transport settings for the local ZONT WebSocket protocol."""

CONNECTION_TIMEOUT = 5.0
COMMAND_TIMEOUT = 10.0
CONTROLLER_INFO_TIMEOUT = 5.0
WS_HEARTBEAT = 30.0
RECONNECT_DELAYS: tuple[float, ...] = (1, 2, 4, 8, 16, 30)
