"""Entities for ZONT user-configured web elements."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

from ..const import DOMAIN
from ..entity import ZontObjectCoordinatorEntity
from ..protocol import (
    ZontCommandTimeoutError,
    ZontConnectionError,
    ZontProtocolError,
)
from ..protocol.heating_commands import (
    ZontCommandRejectedError,
    ZontCommandStateError,
)
from ..protocol.objects import (
    USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON,
    USER_ELEMENT_SUBTYPE_SIMPLE_BUTTON,
    USER_ELEMENT_SUBTYPE_STATUS,
    ZontUserElementData,
)
from ..runtime import ZontRuntimeData
from ..user_element_control import (
    async_press_user_element,
    async_set_user_element_state_and_confirm,
)


class ZontUserElementEntity(ZontObjectCoordinatorEntity):
    """Common presentation for one ZONT user element."""

    _attr_name = None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose the user-configured text without replacing native HA state."""
        element = self._element
        return {"zont_text": element.text if element is not None else None}

    @property
    def _element(self) -> ZontUserElementData | None:
        """Return the current user-element snapshot."""
        obj = self.object_data
        return obj if isinstance(obj, ZontUserElementData) else None


class ZontUserElementStatusBinarySensor(
    ZontUserElementEntity,
    BinarySensorEntity,
):
    """Represent a read-only ZONT input/output status."""

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a user-element status sensor."""
        super().__init__(entry, object_id, "status", None)

    @property
    def available(self) -> bool:
        """Return whether a supported binary status is known."""
        element = self._element
        return (
            super().available
            and element is not None
            and element.subtype == USER_ELEMENT_SUBTYPE_STATUS
            and type(element.raw_state) is int
            and element.raw_state in (0, 1)
        )

    @property
    def is_on(self) -> bool | None:
        """Return the linked input/output status."""
        element = self._element
        if (
            element is None
            or element.subtype != USER_ELEMENT_SUBTYPE_STATUS
            or type(element.raw_state) is not int
            or element.raw_state not in (0, 1)
        ):
            return None
        return element.raw_state == 1


class ZontUserElementButton(ZontUserElementEntity, ButtonEntity):
    """Represent a stateless ZONT user button."""

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a simple user-element button."""
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "button", None)

    @property
    def available(self) -> bool:
        """Return whether the simple button provides its sentinel state."""
        element = self._element
        return (
            super().available
            and element is not None
            and element.subtype == USER_ELEMENT_SUBTYPE_SIMPLE_BUTTON
            and type(element.raw_state) is int
            and element.raw_state == 255
        )

    async def async_press(self) -> None:
        """Execute the action configured for this ZONT button."""
        try:
            await async_press_user_element(self._client, self._object_id)
        except asyncio.CancelledError:
            raise
        except ZontCommandRejectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontCommandTimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_timeout",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="controller_offline",
            ) from err
        except ZontProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protocol_error",
            ) from err


class ZontUserElementSwitch(ZontUserElementEntity, SwitchEntity):
    """Represent a stateful ZONT complex button."""

    def __init__(
        self,
        entry: ConfigEntry[ZontRuntimeData],
        object_id: int,
    ) -> None:
        """Initialize a complex user-element switch."""
        self._client = entry.runtime_data.client
        super().__init__(entry, object_id, "switch", None)

    @property
    def available(self) -> bool:
        """Return whether the complex button provides a binary state."""
        element = self._element
        return (
            super().available
            and element is not None
            and element.subtype == USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON
            and type(element.raw_state) is int
            and element.raw_state in (0, 1)
        )

    @property
    def is_on(self) -> bool | None:
        """Return the observed complex-button state."""
        element = self._element
        if (
            element is None
            or element.subtype != USER_ELEMENT_SUBTYPE_COMPLEX_BUTTON
            or type(element.raw_state) is not int
            or element.raw_state not in (0, 1)
        ):
            return None
        return element.raw_state == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate and confirm this complex button."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Deactivate and confirm this complex button."""
        await self._async_set_state(False)

    async def _async_set_state(self, is_on: bool) -> None:
        """Set a complex-button state and translate protocol failures."""
        try:
            await async_set_user_element_state_and_confirm(
                self._client,
                self.coordinator,
                self._object_id,
                is_on,
            )
        except asyncio.CancelledError:
            raise
        except ZontCommandRejectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontCommandTimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_timeout",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="controller_offline",
            ) from err
        except ZontCommandStateError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="user_element_state_not_confirmed",
                translation_placeholders={"id": str(self._object_id)},
            ) from err
        except ZontProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protocol_error",
            ) from err
