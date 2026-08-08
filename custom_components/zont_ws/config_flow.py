"""Config flow for the ZONT WebSocket integration."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
)
from .const import (
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from .controller import (
    ZontControllerInfo,
    ZontIdentificationError,
    async_identify_controller,
    controller_entry_title,
    controller_websocket_url,
)

_LOGGER = logging.getLogger(__name__)

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_CANNOT_IDENTIFY = "cannot_identify"
ERROR_DIFFERENT_CONTROLLER = "different_controller"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_INVALID_HOST = "invalid_host"
ERROR_UNKNOWN = "unknown"


class ZontWsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZONT WebSocket."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            normalized, info, error = await self._async_validate(user_input)
            if error is None:
                assert info is not None
                await self.async_set_unique_id(info.serial_number)
                self._abort_if_unique_id_configured()
                title = controller_entry_title(info, normalized[CONF_HOST])
                return self.async_create_entry(
                    title=title,
                    data={
                        **normalized,
                        CONF_CONTROLLER: info.as_dict(),
                        CONF_AUTO_TITLE: title,
                    },
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the controller connection settings to be changed."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = str(user_input.get(CONF_PASSWORD, ""))
            candidate = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: password or entry.data[CONF_PASSWORD],
            }
            normalized, info, error = await self._async_validate(candidate)
            if error is None:
                assert info is not None
                if not _controller_matches_entry(entry, info):
                    errors["base"] = ERROR_DIFFERENT_CONTROLLER
                    return self.async_show_form(
                        step_id="reconfigure",
                        data_schema=_reconfigure_schema(user_input),
                        errors=errors,
                    )

                title = controller_entry_title(info, normalized[CONF_HOST])
                return self.async_update_reload_and_abort(
                    entry,
                    title=(title if _entry_title_is_managed(entry) else entry.title),
                    data_updates={
                        CONF_HOST: normalized[CONF_HOST],
                        CONF_USERNAME: normalized[CONF_USERNAME],
                        CONF_PASSWORD: normalized[CONF_PASSWORD],
                        CONF_CONTROLLER: info.as_dict(),
                        CONF_AUTO_TITLE: title,
                    },
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema(user_input or entry.data),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start a reauthentication flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {
                CONF_HOST: entry.data[CONF_HOST],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            normalized, info, error = await self._async_validate(candidate)
            if error is None:
                assert info is not None
                if not _controller_matches_entry(entry, info):
                    errors["base"] = ERROR_DIFFERENT_CONTROLLER
                    return self.async_show_form(
                        step_id="reauth_confirm",
                        data_schema=_reauth_schema(user_input[CONF_USERNAME]),
                        errors=errors,
                    )

                title = controller_entry_title(info, normalized[CONF_HOST])
                return self.async_update_reload_and_abort(
                    entry,
                    title=(title if _entry_title_is_managed(entry) else entry.title),
                    data_updates={
                        CONF_USERNAME: normalized[CONF_USERNAME],
                        CONF_PASSWORD: normalized[CONF_PASSWORD],
                        CONF_CONTROLLER: info.as_dict(),
                        CONF_AUTO_TITLE: title,
                    },
                )
            errors["base"] = error

        suggested_username = (
            user_input[CONF_USERNAME]
            if user_input is not None
            else entry.data[CONF_USERNAME]
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(suggested_username),
            errors=errors,
        )

    async def _async_validate(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], ZontControllerInfo | None, str | None]:
        """Normalize and validate configuration data."""
        try:
            host = _normalize_host(user_input[CONF_HOST])
        except ValueError:
            return {}, None, ERROR_INVALID_HOST

        username = str(user_input[CONF_USERNAME]).strip()
        password = str(user_input[CONF_PASSWORD])
        if not username or not password:
            return {}, None, ERROR_INVALID_AUTH

        normalized = {
            CONF_HOST: host,
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
        }
        try:
            info = await async_identify_controller(
                async_get_clientsession(self.hass),
                controller_websocket_url(host),
                ZontCredentials(username=username, password=password),
            )
        except ZontAuthenticationError:
            return {}, None, ERROR_INVALID_AUTH
        except ZontIdentificationError:
            return {}, None, ERROR_CANNOT_IDENTIFY
        except (ZontConnectionError, ZontProtocolError):
            return {}, None, ERROR_CANNOT_CONNECT
        except Exception:
            _LOGGER.exception("Unexpected error while validating ZONT connection")
            return {}, None, ERROR_UNKNOWN

        return normalized, info, None


def _normalize_host(value: Any) -> str:
    """Return a canonical IPv4 or IPv6 address, or raise ValueError."""
    return str(ip_address(str(value).strip()))


def _password_selector() -> TextSelector:
    """Return a password text selector."""
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _reconfigure_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Return the reconfiguration schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST,
                default=defaults.get(CONF_HOST, ""),
            ): TextSelector(),
            vol.Required(
                CONF_USERNAME,
                default=defaults.get(CONF_USERNAME, ""),
            ): TextSelector(),
            vol.Optional(CONF_PASSWORD): _password_selector(),
        }
    )


def _reauth_schema(username: str) -> vol.Schema:
    """Return the reauthentication schema."""
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=username): TextSelector(),
            vol.Required(CONF_PASSWORD): _password_selector(),
        }
    )


def _controller_matches_entry(
    entry: config_entries.ConfigEntry,
    info: ZontControllerInfo,
) -> bool:
    """Return whether discovered identity belongs to the configured controller."""
    existing = entry.unique_id
    if existing is None:
        cached = ZontControllerInfo.from_mapping(entry.data.get(CONF_CONTROLLER))
        existing = cached.serial_number if cached is not None else None
    return existing is None or existing == info.serial_number


def _entry_title_is_managed(entry: config_entries.ConfigEntry) -> bool:
    """Return whether the integration may replace the config entry title."""
    previous_title = entry.data.get(CONF_AUTO_TITLE)
    return entry.title == previous_title or entry.title == "ZONT WebSocket"


def _user_schema(user_input: dict[str, Any] | None) -> vol.Schema:
    """Return the user configuration schema."""
    schema: dict[vol.Marker, Any] = {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): _password_selector(),
    }
    if user_input is not None:
        schema = {
            vol.Required(
                CONF_HOST, default=user_input.get(CONF_HOST, "")
            ): TextSelector(),
            vol.Required(
                CONF_USERNAME,
                default=user_input.get(CONF_USERNAME, ""),
            ): TextSelector(),
            vol.Required(CONF_PASSWORD): _password_selector(),
        }
    return vol.Schema(schema)
