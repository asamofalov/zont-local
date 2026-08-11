"""Tests for ZONT diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.zont_local.const import CONF_CONTROLLER, DOMAIN
from custom_components.zont_local.coordinator import (
    ZontDataUpdateCoordinator,
)
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.diagnostics import async_get_config_entry_diagnostics
from custom_components.zont_local.protocol import ZontClient
from custom_components.zont_local.protocol.controller import (
    ZontCommunicationChannel,
    ZontControllerInfo,
    ZontGsmRegistrationState,
    ZontGsmStatus,
    ZontPowerSource,
    ZontServerStatus,
    ZontWifiStatus,
)
from custom_components.zont_local.runtime import ZontRuntimeData
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_diagnostics_exclude_credentials(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_USERNAME: "secret-user",
            CONF_PASSWORD: "secret-password",
            CONF_CONTROLLER: ZontControllerInfo(
                serial_number="ABCDEF123456",
                model="H1V02 PRO",
                board_model="700",
                firmware_version="625",
            ).as_dict(),
        },
    )
    client = MagicMock(spec=ZontClient)
    client.is_connected = True
    client.last_error = None
    client.reconnect_count = 2
    client.pending_count = 1
    coordinator = MagicMock(spec=ZontDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.data = ZontData(
        controller=ZontControllerData(
            info=None,
            server_status=ZontServerStatus(
                cloud_connected=True,
                channels=frozenset({ZontCommunicationChannel.WIFI}),
            ),
            supply_voltage=12.3,
            power_source=ZontPowerSource.MAIN,
            wifi_status=ZontWifiStatus(connected=True, raw_signal=86),
            gsm_status=ZontGsmStatus(ZontGsmRegistrationState.SEARCHING, 0),
        )
    )
    entry.runtime_data = ZontRuntimeData(client, coordinator)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result == {
        "config": {"host": "**REDACTED**"},
        "controller": {
            "model": "H1V02 PRO",
            "board_model": "700",
            "firmware_version": "625",
        },
        "connection": {
            "connected": True,
            "last_error": None,
            "reconnect_count": 2,
            "pending_commands": 1,
        },
        "exports": {
            "configured": 0,
            "active": 0,
            "errors": 0,
        },
        "data": {
            "last_update_success": True,
            "cloud_connected": True,
            "connection_channels": ["wifi"],
            "supply_voltage": 12.3,
            "power_source": "main",
            "wifi_connected": True,
            "wifi_signal_percent": 28,
            "ethernet_connected": None,
            "gsm_registration": "searching",
            "gsm_signal_percent": 0,
        },
    }
    assert "secret-user" not in str(result)
    assert "secret-password" not in str(result)
    assert "192.0.2.10" not in str(result)
    assert "ABCDEF123456" not in str(result)
    assert "02:00:00:00:00:01" not in str(result)
    assert "Test Operator" not in str(result)
