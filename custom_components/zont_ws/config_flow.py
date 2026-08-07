"""Config flow for the ZONT WebSocket integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from yarl import URL

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    async_validate_connection,
)
from .const import CONFIG_ENTRY_VERSION, DOMAIN

_LOGGER = logging.getLogger(__name__)

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_INVALID_URL = "invalid_url"
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
            normalized, error = await self._async_validate(user_input)
            if error is None:
                return self.async_create_entry(title="ZONT WebSocket", data=normalized)
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the controller URL to be changed."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {
                CONF_URL: user_input[CONF_URL],
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_PASSWORD: entry.data[CONF_PASSWORD],
            }
            normalized, error = await self._async_validate(candidate)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_URL: normalized[CONF_URL]},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL,
                        default=(
                            user_input[CONF_URL]
                            if user_input is not None
                            else entry.data[CONF_URL]
                        ),
                    ): _url_selector(),
                }
            ),
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
                CONF_URL: entry.data[CONF_URL],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            normalized, error = await self._async_validate(candidate)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: normalized[CONF_USERNAME],
                        CONF_PASSWORD: normalized[CONF_PASSWORD],
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
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=suggested_username,
                    ): TextSelector(),
                    vol.Required(CONF_PASSWORD): _password_selector(),
                }
            ),
            errors=errors,
        )

    async def _async_validate(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], str | None]:
        """Normalize and validate configuration data."""
        try:
            url = _normalize_url(user_input[CONF_URL])
        except ValueError:
            return {}, ERROR_INVALID_URL

        username = str(user_input[CONF_USERNAME]).strip()
        password = str(user_input[CONF_PASSWORD])
        if not username or not password:
            return {}, ERROR_INVALID_AUTH

        normalized = {
            CONF_URL: url,
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
        }
        try:
            await async_validate_connection(
                async_get_clientsession(self.hass),
                url,
                ZontCredentials(username=username, password=password),
            )
        except ZontAuthenticationError:
            return {}, ERROR_INVALID_AUTH
        except (ZontConnectionError, ZontProtocolError):
            return {}, ERROR_CANNOT_CONNECT
        except Exception:
            _LOGGER.exception("Unexpected error while validating ZONT connection")
            return {}, ERROR_UNKNOWN

        return normalized, None


def _normalize_url(value: Any) -> str:
    """Return a normalized ws/wss URL or raise ValueError."""
    raw_url = str(value).strip()
    try:
        url = URL(raw_url)
    except (TypeError, ValueError) as err:
        raise ValueError from err
    if url.scheme not in {"ws", "wss"} or not url.host:
        raise ValueError
    return str(url)


def _url_selector() -> TextSelector:
    """Return a URL text selector."""
    return TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


def _password_selector() -> TextSelector:
    """Return a password text selector."""
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _user_schema(user_input: dict[str, Any] | None) -> vol.Schema:
    """Return the user configuration schema."""
    schema: dict[vol.Marker, Any] = {
        vol.Required(CONF_URL): _url_selector(),
        vol.Required(CONF_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): _password_selector(),
    }
    if user_input is not None:
        schema = {
            vol.Required(
                CONF_URL, default=user_input.get(CONF_URL, "")
            ): _url_selector(),
            vol.Required(
                CONF_USERNAME,
                default=user_input.get(CONF_USERNAME, ""),
            ): TextSelector(),
            vol.Required(CONF_PASSWORD): _password_selector(),
        }
    return vol.Schema(schema)
