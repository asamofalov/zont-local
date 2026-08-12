"""Transport settings for the local ZONT WebSocket protocol."""

CONNECTION_TIMEOUT = 5.0
REQUEST_TIMEOUT = 10.0
WS_HEARTBEAT = 60.0
RECONNECT_DELAYS: tuple[float, ...] = (1, 2, 4, 8, 16, 30)
