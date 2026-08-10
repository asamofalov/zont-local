"""Home Assistant-facing names for ZONT controller data."""

from .protocol.controller import ZontControllerInfo, controller_endpoint


def controller_entry_title(info: ZontControllerInfo | None, host: str) -> str:
    """Build the integration-managed config entry title."""
    endpoint = controller_endpoint(host)
    if info is not None and info.model is not None:
        return f"ZONT {info.model} ({endpoint})"
    return f"Контроллер ZONT ({endpoint})"


def controller_device_name(info: ZontControllerInfo | None) -> str:
    """Build the integration-managed device name."""
    if info is not None and info.model is not None:
        return f"ZONT {info.model}"
    return "Контроллер ZONT"
