"""Config flow for the ZONT WebSocket integration."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
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
    async_open_temporary_request_session,
)
from .const import (
    CONF_AUTO_IMPORT_NEW_OBJECTS,
    CONF_AUTO_TITLE,
    CONF_CONTROLLER,
    CONF_DHW_ON_TEMPERATURE,
    CONF_EXCLUDED_OBJECT_IDS,
    CONF_HEATING_OFF_MODE_ID,
    CONF_IMPORTED_OBJECT_IDS,
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
from .flow_helpers import (
    ERROR_CANNOT_CONNECT,
    ERROR_CANNOT_IDENTIFY,
    ERROR_CANNOT_READ_DEVICES,
    ERROR_CANNOT_READ_MODES,
    ERROR_DIFFERENT_CONTROLLER,
    ERROR_INVALID_AUTH,
    ERROR_INVALID_DEVICE_SELECTION,
    ERROR_INVALID_DHW_ON_TEMPERATURE,
    ERROR_INVALID_HOST,
    ERROR_INVALID_OFF_MODE,
    ERROR_INVALID_SCAN_INTERVAL,
    ERROR_NO_OFF_MODE,
    ERROR_UNKNOWN,
    _devices_schema,
    _heating_mode_schema,
    _validate_dhw_on_temperature,
    _validate_scan_interval,
    _validate_selected_object_ids,
)
from .heating_config import ZontHeatingModeConfiguration
from .heating_modes import (
    ZontHeatingModeDiscovery,
    async_discover_heating_modes_from_requests,
)
from .object_discovery import (
    ZontObjectDiscoveryError,
    async_discover_importable_objects_from_requests,
)
from .object_import import importable_object_descriptors
from .objects import ZontObject

_LOGGER = logging.getLogger(__name__)


class _InitialConfigFlowSteps:
    """Implement the multi-step initial controller configuration."""

    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        """Initialize pending multi-step configuration data."""
        self._pending_data: dict[str, Any] | None = None
        self._pending_options: dict[str, Any] | None = None
        self._pending_title: str | None = None
        self._off_modes: tuple[ZontHeatingModeConfiguration, ...] = ()
        self._objects: dict[int, ZontObject] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            (
                normalized,
                info,
                off_modes,
                objects,
                error,
            ) = await self._async_validate_user(user_input)
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
                    self._objects = dict(objects)
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
                temperature = _validate_dhw_on_temperature(user_input)
                if temperature is None:
                    errors["base"] = ERROR_INVALID_DHW_ON_TEMPERATURE
                elif (scan_interval := _validate_scan_interval(user_input)) is None:
                    errors["base"] = ERROR_INVALID_SCAN_INTERVAL
                elif mode_id not in {mode.object_id for mode in self._off_modes}:
                    errors["base"] = ERROR_INVALID_OFF_MODE
                else:
                    self._pending_options = {
                        CONF_HEATING_OFF_MODE_ID: mode_id,
                        CONF_DHW_ON_TEMPERATURE: temperature,
                        CONF_SCAN_INTERVAL: scan_interval,
                    }
                    return await self.async_step_devices()

        return self.async_show_form(
            step_id="heating_mode",
            data_schema=_heating_mode_schema(self._off_modes),
            errors=errors,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select which discovered ZONT objects are exposed in Home Assistant."""
        if (
            self._pending_data is None
            or self._pending_options is None
            or self._pending_title is None
        ):
            return self.async_abort(reason=ERROR_UNKNOWN)

        descriptors = importable_object_descriptors(self._objects)
        valid_ids = frozenset(descriptor.object_id for descriptor in descriptors)
        errors: dict[str, str] = {}
        if user_input is not None:
            selected_ids = _validate_selected_object_ids(user_input, valid_ids)
            auto_import = user_input.get(CONF_AUTO_IMPORT_NEW_OBJECTS)
            if selected_ids is None or type(auto_import) is not bool:
                errors["base"] = ERROR_INVALID_DEVICE_SELECTION
            else:
                return self.async_create_entry(
                    title=self._pending_title,
                    data=self._pending_data,
                    options={
                        **self._pending_options,
                        CONF_IMPORTED_OBJECT_IDS: sorted(selected_ids),
                        CONF_EXCLUDED_OBJECT_IDS: sorted(valid_ids - selected_ids),
                        CONF_AUTO_IMPORT_NEW_OBJECTS: auto_import,
                    },
                )

        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(
                descriptors,
                valid_ids,
                auto_import_new=True,
            ),
            errors=errors,
        )


class ZontWsConfigFlow(
    _InitialConfigFlowSteps,
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Handle a config flow for ZONT WebSocket."""

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
                update = {
                    "title": (title if _entry_title_is_managed(entry) else entry.title),
                    "data_updates": {
                        CONF_HOST: normalized[CONF_HOST],
                        CONF_USERNAME: normalized[CONF_USERNAME],
                        CONF_PASSWORD: normalized[CONF_PASSWORD],
                        CONF_CONTROLLER: info.as_dict(),
                        CONF_AUTO_TITLE: title,
                    },
                }
                if entry.state is config_entries.ConfigEntryState.LOADED:
                    return self.async_update_and_abort(entry, **update)
                return self.async_update_reload_and_abort(entry, **update)
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
                update = {
                    "title": (title if _entry_title_is_managed(entry) else entry.title),
                    "data_updates": {
                        CONF_USERNAME: normalized[CONF_USERNAME],
                        CONF_PASSWORD: normalized[CONF_PASSWORD],
                        CONF_CONTROLLER: info.as_dict(),
                        CONF_AUTO_TITLE: title,
                    },
                }
                if entry.state is config_entries.ConfigEntryState.LOADED:
                    return self.async_update_and_abort(entry, **update)
                return self.async_update_reload_and_abort(entry, **update)
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
        dict[int, ZontObject],
        str | None,
    ]:
        """Validate initial setup and discover modes on one connection."""
        normalized, normalization_error = _normalize_connection_data(user_input)
        if normalization_error is not None:
            return {}, None, (), {}, normalization_error

        try:
            info, discovery, objects = await _async_identify_and_discover_configuration(
                self.hass, normalized
            )
        except ZontAuthenticationError:
            return {}, None, (), {}, ERROR_INVALID_AUTH
        except ZontIdentificationError:
            return {}, None, (), {}, ERROR_CANNOT_IDENTIFY
        except ZontConnectionError:
            return {}, None, (), {}, ERROR_CANNOT_CONNECT
        except ZontObjectDiscoveryError:
            _LOGGER.debug(
                "Unable to discover ZONT objects during initial setup",
                exc_info=True,
            )
            return {}, None, (), {}, ERROR_CANNOT_READ_DEVICES
        except ZontProtocolError:
            _LOGGER.debug(
                "Unable to discover ZONT heating modes during initial setup",
                exc_info=True,
            )
            return {}, None, (), {}, ERROR_CANNOT_READ_MODES
        except Exception:
            _LOGGER.exception("Unexpected error while configuring ZONT")
            return {}, None, (), {}, ERROR_UNKNOWN

        return normalized, info, discovery.eligible_off_modes, dict(objects), None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Create the options flow for controller behavior."""
        from .options_flow import ZontWsOptionsFlow

        return ZontWsOptionsFlow()


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


async def _async_identify_and_discover_configuration(
    hass: HomeAssistant,
    data: dict[str, str],
) -> tuple[ZontControllerInfo, ZontHeatingModeDiscovery, dict[int, ZontObject]]:
    """Identify a controller, heating modes and objects on one connection."""
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
        objects = await async_discover_importable_objects_from_requests(requests)
    return info, discovery, dict(objects)


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
