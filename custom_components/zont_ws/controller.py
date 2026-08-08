"""Controller identity helpers for the ZONT integration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Self

from aiohttp import ClientSession
from yarl import URL

from .client import (
    ZontCredentials,
    ZontProtocolError,
    ZontRequestTimeoutError,
    ZontWsClient,
    async_request_system_commands,
)
from .const import CONTROLLER_INFO_TIMEOUT

_SERIAL_PATTERN = re.compile(r"[A-Za-z0-9]{12}")
_INFO_VALUE_PATTERN = re.compile(r"[A-Za-z0-9._+-]+")

COMMAND_CONTROLLER_INFO = "#S7?"
COMMAND_SERVER_INFO = "#S224?"
COMMAND_SUPPLY_VOLTAGE = "#S6?"
COMMAND_RESTART = "SRESTART?"


class ZontIdentificationError(ZontProtocolError):
    """Raised when required controller identity cannot be obtained."""


class ZontCommunicationChannel(StrEnum):
    """Communication channels reported by a ZONT controller."""

    GSM = "gsm"
    WIFI = "wifi"
    ETHERNET = "ethernet"


@dataclass(frozen=True, slots=True)
class ZontServerStatus:
    """Cloud and communication-channel state reported by a controller."""

    cloud_connected: bool
    channels: frozenset[ZontCommunicationChannel]

    @property
    def channel_state(self) -> str:
        """Return a stable Home Assistant enum state for active channels."""
        if not self.channels:
            return "none"
        return "_".join(
            channel.value
            for channel in ZontCommunicationChannel
            if channel in self.channels
        )


@dataclass(frozen=True, slots=True)
class ZontControllerInfo:
    """Stable identity and descriptive data reported by a controller."""

    serial_number: str
    model: str | None = None
    board_model: str | None = None
    firmware_version: str | None = None

    def with_identity_response(self, response: str) -> Self:
        """Return controller data enriched from a #S7 response."""
        model, board_model, firmware_version = parse_identity_response(response)
        return replace(
            self,
            model=model,
            board_model=board_model,
            firmware_version=firmware_version,
        )

    def as_dict(self) -> dict[str, str]:
        """Return a config-entry-safe representation."""
        data = {"serial_number": self.serial_number}
        if self.model is not None:
            data["model"] = self.model
        if self.board_model is not None:
            data["board_model"] = self.board_model
        if self.firmware_version is not None:
            data["firmware_version"] = self.firmware_version
        return data

    @classmethod
    def from_mapping(cls, value: Any) -> Self | None:
        """Restore validated controller data from config entry storage."""
        if not isinstance(value, Mapping):
            return None

        serial_number = value.get("serial_number")
        if not isinstance(serial_number, str) or not _SERIAL_PATTERN.fullmatch(
            serial_number
        ):
            return None

        optional_values: dict[str, str | None] = {}
        for key in ("model", "board_model", "firmware_version"):
            item = value.get(key)
            optional_values[key] = item if isinstance(item, str) and item else None
        return cls(serial_number=serial_number, **optional_values)


def parse_serial_response(response: str) -> str:
    """Return the serial number from a strict #S54 response."""
    prefix = "#S54:"
    if not response.startswith(prefix):
        raise ValueError("Unexpected serial number response")
    serial_number = response.removeprefix(prefix).strip()
    if not _SERIAL_PATTERN.fullmatch(serial_number):
        raise ValueError("Invalid controller serial number")
    return serial_number.upper()


def parse_identity_response(response: str) -> tuple[str, str, str]:
    """Return model, board model and firmware from a strict #S7 response."""
    prefix = "#S7:"
    if not response.startswith(prefix):
        raise ValueError("Unexpected controller identity response")
    values = response.removeprefix(prefix).split()
    if len(values) != 3 or any(
        _INFO_VALUE_PATTERN.fullmatch(value) is None for value in values
    ):
        raise ValueError("Invalid controller identity response")

    model, board_model, firmware_version = values
    return model.replace("_", " "), board_model, firmware_version


def parse_server_status_response(response: str) -> ZontServerStatus:
    """Return cloud and communication status from a strict #S224 response."""
    prefix = "#S224:"
    if not response.startswith(prefix):
        raise ValueError("Unexpected server status response")

    values = response.removeprefix(prefix).split()
    if len(values) != 4 or any(value not in {"0", "1"} for value in values):
        raise ValueError("Invalid server status response")

    channels = frozenset(
        channel
        for channel, enabled in zip(
            ZontCommunicationChannel,
            values[1:],
            strict=True,
        )
        if enabled == "1"
    )
    return ZontServerStatus(
        cloud_connected=values[0] == "1",
        channels=channels,
    )


def parse_supply_voltage_response(response: str) -> float:
    """Return controller supply voltage from a strict #S6 response."""
    prefix = "#S6:"
    if not response.startswith(prefix):
        raise ValueError("Unexpected supply voltage response")

    values = response.removeprefix(prefix).split()
    if len(values) != 2 or not values[0].isdigit():
        raise ValueError("Invalid supply voltage response")
    return int(values[0]) / 10


async def async_identify_controller(
    session: ClientSession,
    url: str,
    credentials: ZontCredentials,
) -> ZontControllerInfo:
    """Authenticate and obtain all identity required by config flow."""
    try:
        serial_response, identity_response = await async_request_system_commands(
            session,
            url,
            credentials,
            ("#S54?", "#S7?"),
            response_timeout=CONTROLLER_INFO_TIMEOUT,
        )
        serial_number = parse_serial_response(serial_response)
        return ZontControllerInfo(serial_number).with_identity_response(
            identity_response
        )
    except (ZontProtocolError, ZontRequestTimeoutError, ValueError) as err:
        raise ZontIdentificationError(
            "Unable to obtain complete controller identity"
        ) from err


async def async_refresh_controller_info(
    client: ZontWsClient,
    serial_number: str,
) -> ZontControllerInfo:
    """Refresh descriptive data through an active protocol client."""
    response = await client.async_send_system_command(
        COMMAND_CONTROLLER_INFO, response_timeout=CONTROLLER_INFO_TIMEOUT
    )
    try:
        return ZontControllerInfo(serial_number).with_identity_response(response)
    except ValueError as err:
        raise ZontProtocolError("Invalid controller identity response") from err


async def async_restart_controller(client: ZontWsClient) -> None:
    """Restart a controller without waiting for an optional response."""
    await client.async_send_system_command_without_response(COMMAND_RESTART)


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


def controller_endpoint(host: str) -> str:
    """Return a controller IP formatted for display."""
    if ":" in host:
        return f"[{host}]"
    return host


def controller_websocket_url(host: str) -> str:
    """Return the local WebSocket endpoint for a controller IP."""
    return str(URL.build(scheme="ws", host=host, path="/ws"))


def controller_configuration_url(host: str) -> str:
    """Return the HTTP origin for the controller web interface."""
    return str(URL.build(scheme="http", host=host))
