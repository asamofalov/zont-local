"""Transport settings for the local ZONT WebSocket protocol."""

CONNECTION_TIMEOUT = 5.0
REQUEST_TIMEOUT = 10.0
WS_HEARTBEAT = 60.0
RECONNECT_DELAYS: tuple[float, ...] = (5, 10, 20, 30, 60)
RECONNECT_STABLE_SECONDS = 60.0
