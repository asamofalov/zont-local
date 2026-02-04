from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN, CONF_URL, CONF_USER, CONF_PASS


class ZontWsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(CONF_URL, default="ws://10.79.5.110/ws"): str,
                    vol.Required(CONF_USER): str,
                    vol.Required(CONF_PASS): str,
                }
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        # Один инстанс интеграции
        await self.async_set_unique_id("zont_ws_singleton")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(title="ZONT WS", data=user_input)