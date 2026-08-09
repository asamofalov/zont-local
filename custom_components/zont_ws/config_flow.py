"""Config flow for the ZONT WebSocket integration."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import Any, cast

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import (
    ZontAuthenticationError,
    ZontConnectionError,
    ZontCredentials,
    ZontProtocolError,
    async_open_temporary_request_session,
)
from .const import (
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONF_HEATING_OFF_MODE_ID,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from .controller import (
    ZontControllerInfo,
    ZontIdentificationError,
    async_identify_controller,
    async_identify_controller_from_requests,
    controller_entry_title,
    controller_websocket_url,
)
from .coordinator import ZontData, ZontRuntimeData
from .heating_config import ZontHeatingModeConfiguration
from .heating_modes import (
    ZontHeatingModeDiscovery,
    async_discover_heating_modes,
    async_discover_heating_modes_from_requests,
    eligible_off_modes,
    relevant_heating_circuit_ids,
)

_LOGGER = logging.getLogger(__name__)

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_CANNOT_IDENTIFY = "cannot_identify"
ERROR_CANNOT_READ_MODES = "cannot_read_modes"
ERROR_DIFFERENT_CONTROLLER = "different_controller"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_INVALID_HOST = "invalid_host"
ERROR_INVALID_OFF_MODE = "invalid_off_mode"
ERROR_NO_OFF_MODE = "no_off_mode"
ERROR_UNKNOWN = "unknown"


class ZontWsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZONT WebSocket."""

    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        """Initialize pending multi-step configuration data."""
        self._pending_data: dict[str, Any] | None = None
        self._pending_title: str | None = None
        self._off_modes: tuple[ZontHeatingModeConfiguration, ...] = ()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            normalized, info, off_modes, error = await self._async_validate_user(
                user_input
            )
            if error is None:
                assert info is not None
                await self.async_set_unique_id(info.serial_number)
                self._abort_if_unique_id_configured()
                title = controller_entry_title(info, normalized[CONF_HOST])
                if not off_modes:
                    return self.async_abort(reason=ERROR_NO_OFF_MODE)
                else:
                    self._pending_title = title
                    self._pending_data = {
                        **normalized,
                        CONF_CONTROLLER: info.as_dict(),
                        CONF_AUTO_TITLE: title,
                    }
                    self._off_modes = off_modes
                    return await self.async_step_heating_mode()
            else:
                errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_heating_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the controller mode used to disable all heating circuits."""
        if self._pending_data is None or self._pending_title is None:
            return self.async_abort(reason=ERROR_UNKNOWN)

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mode_id = int(user_input[CONF_HEATING_OFF_MODE_ID])
            except (KeyError, TypeError, ValueError):
                errors["base"] = ERROR_INVALID_OFF_MODE
            else:
                if mode_id not in {mode.object_id for mode in self._off_modes}:
                    errors["base"] = ERROR_INVALID_OFF_MODE
                else:
                    return self.async_create_entry(
                        title=self._pending_title,
                        data=self._pending_data,
                        options={CONF_HEATING_OFF_MODE_ID: mode_id},
                    )

        return self.async_show_form(
            step_id="heating_mode",
            data_schema=_heating_mode_schema(self._off_modes),
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
        normalized, normalization_error = _normalize_connection_data(user_input)
        if normalization_error is not None:
            return {}, None, normalization_error
        try:
            info = await async_identify_controller(
                async_get_clientsession(self.hass),
                controller_websocket_url(normalized[CONF_HOST]),
                ZontCredentials(
                    username=normalized[CONF_USERNAME],
                    password=normalized[CONF_PASSWORD],
                ),
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

    async def _async_validate_user(
        self, user_input: dict[str, Any]
    ) -> tuple[
        dict[str, str],
        ZontControllerInfo | None,
        tuple[ZontHeatingModeConfiguration, ...],
        str | None,
    ]:
        """Validate initial setup and discover modes on one connection."""
        normalized, normalization_error = _normalize_connection_data(user_input)
        if normalization_error is not None:
            return {}, None, (), normalization_error

        try:
            info, discovery = await _async_identify_and_discover_heating_modes(
                self.hass, normalized
            )
        except ZontAuthenticationError:
            return {}, None, (), ERROR_INVALID_AUTH
        except ZontIdentificationError:
            return {}, None, (), ERROR_CANNOT_IDENTIFY
        except ZontConnectionError:
            return {}, None, (), ERROR_CANNOT_CONNECT
        except ZontProtocolError:
            _LOGGER.debug(
                "Unable to discover ZONT heating modes during initial setup",
                exc_info=True,
            )
            return {}, None, (), ERROR_CANNOT_READ_MODES
        except Exception:
            _LOGGER.exception("Unexpected error while configuring ZONT")
            return {}, None, (), ERROR_UNKNOWN

        return normalized, info, discovery.eligible_off_modes, None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ZontWsOptionsFlow:
        """Create the options flow for controller behavior."""
        return ZontWsOptionsFlow()


class ZontWsOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage changeable ZONT integration behavior."""

    def __init__(self) -> None:
        """Initialize options discovered for this flow instance."""
        self._off_modes: tuple[ZontHeatingModeConfiguration, ...] = ()
        self._off_modes_error: str | None = None
        self._off_modes_loaded = False

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the current controller-wide off mode."""
        if not self._off_modes_loaded:
            self._off_modes, self._off_modes_error = await _async_get_entry_off_modes(
                self.hass, self.config_entry
            )
            self._off_modes_loaded = True
        off_modes = self._off_modes
        error = self._off_modes_error
        if error is not None or not off_modes:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                errors={"base": error or ERROR_NO_OFF_MODE},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mode_id = int(user_input[CONF_HEATING_OFF_MODE_ID])
            except (KeyError, TypeError, ValueError):
                errors["base"] = ERROR_INVALID_OFF_MODE
            else:
                if mode_id in {mode.object_id for mode in off_modes}:
                    return self.async_create_entry(
                        data={CONF_HEATING_OFF_MODE_ID: mode_id}
                    )
                errors["base"] = ERROR_INVALID_OFF_MODE

        current_mode_id = self.config_entry.options.get(CONF_HEATING_OFF_MODE_ID)
        return self.async_show_form(
            step_id="init",
            data_schema=_heating_mode_schema(
                off_modes,
                current_mode_id if type(current_mode_id) is int else None,
            ),
            errors=errors,
        )


def _normalize_host(value: Any) -> str:
    """Return a canonical IPv4 or IPv6 address, or raise ValueError."""
    return str(ip_address(str(value).strip()))


def _normalize_connection_data(
    user_input: dict[str, Any],
) -> tuple[dict[str, str], str | None]:
    """Normalize connection fields shared by configuration flows."""
    try:
        host = _normalize_host(user_input[CONF_HOST])
    except ValueError:
        return {}, ERROR_INVALID_HOST

    username = str(user_input[CONF_USERNAME]).strip()
    password = str(user_input[CONF_PASSWORD])
    if not username or not password:
        return {}, ERROR_INVALID_AUTH
    return {
        CONF_HOST: host,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
    }, None


async def _async_identify_and_discover_heating_modes(
    hass: HomeAssistant,
    data: dict[str, str],
) -> tuple[ZontControllerInfo, ZontHeatingModeDiscovery]:
    """Identify a controller and discover its modes on one connection."""
    async with async_open_temporary_request_session(
        async_get_clientsession(hass),
        controller_websocket_url(data[CONF_HOST]),
        ZontCredentials(
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
        ),
    ) as requests:
        info = await async_identify_controller_from_requests(requests)
        discovery = await async_discover_heating_modes_from_requests(requests)
    return info, discovery


async def _async_discover_off_modes(
    hass: HomeAssistant,
    data: dict[str, str],
) -> tuple[tuple[ZontHeatingModeConfiguration, ...], str | None]:
    """Return modes proven to disable all supported heating circuits."""
    try:
        discovery = await async_discover_heating_modes(
            async_get_clientsession(hass),
            controller_websocket_url(data[CONF_HOST]),
            ZontCredentials(
                username=data[CONF_USERNAME],
                password=data[CONF_PASSWORD],
            ),
        )
    except ZontAuthenticationError:
        return (), ERROR_INVALID_AUTH
    except ZontConnectionError:
        return (), ERROR_CANNOT_CONNECT
    except ZontProtocolError:
        _LOGGER.debug("Unable to discover ZONT heating modes", exc_info=True)
        return (), ERROR_CANNOT_READ_MODES
    except Exception:
        _LOGGER.exception("Unexpected error while discovering ZONT heating modes")
        return (), ERROR_UNKNOWN
    return discovery.eligible_off_modes, None


async def _async_get_entry_off_modes(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> tuple[tuple[ZontHeatingModeConfiguration, ...], str | None]:
    """Return off modes without opening a competing controller connection."""
    if entry.state is not ConfigEntryState.LOADED:
        return await _async_discover_off_modes(
            hass,
            {
                CONF_HOST: entry.data[CONF_HOST],
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_PASSWORD: entry.data[CONF_PASSWORD],
            },
        )

    runtime_data = cast(ZontRuntimeData, entry.runtime_data)
    if not runtime_data.client.is_connected:
        return (), ERROR_CANNOT_CONNECT

    coordinator = runtime_data.coordinator
    if not _heating_mode_data_is_complete(coordinator.data):
        await coordinator.async_request_refresh()
        if not coordinator.last_update_success:
            return (), ERROR_CANNOT_CONNECT

    data = coordinator.data
    if not _heating_mode_data_is_complete(data):
        return (), ERROR_CANNOT_READ_MODES
    return eligible_off_modes(
        data.objects,
        data.heating_states,
        data.heating_modes,
    ), None


def _heating_mode_data_is_complete(data: ZontData) -> bool:
    """Return whether a coordinator snapshot can validate an off mode."""
    circuit_ids = relevant_heating_circuit_ids(data.objects)
    return bool(
        circuit_ids and data.heating_modes and circuit_ids.issubset(data.heating_states)
    )


def _heating_mode_schema(
    modes: tuple[ZontHeatingModeConfiguration, ...],
    default: int | None = None,
) -> vol.Schema:
    """Return a required selector for an eligible controller mode."""
    options = [
        SelectOptionDict(
            value=str(mode.object_id),
            label=f"{mode.name} (ID {mode.object_id})",
        )
        for mode in modes
    ]
    marker: vol.Marker = vol.Required(CONF_HEATING_OFF_MODE_ID)
    if default is not None and any(mode.object_id == default for mode in modes):
        marker = vol.Required(CONF_HEATING_OFF_MODE_ID, default=str(default))
    return vol.Schema(
        {
            marker: SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    custom_value=False,
                )
            )
        }
    )


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
