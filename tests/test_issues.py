"""Tests for ZONT Local Repair issue lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.zont_local import async_remove_entry
from custom_components.zont_local.const import (
    CONF_EXPORTS,
    CONF_HEATING_OFF_MODE_ID,
    DOMAIN,
)
from custom_components.zont_local.coordinator import ZontDataUpdateCoordinator
from custom_components.zont_local.data import ZontControllerData, ZontData
from custom_components.zont_local.export import ZontExportBinding, ZontExportKind
from custom_components.zont_local.export.model import export_issue_id
from custom_components.zont_local.issues import (
    async_set_heating_off_mode_issue,
    heating_off_mode_issue_id,
)
from custom_components.zont_local.protocol import ZontClient
from custom_components.zont_local.protocol.heating_config import (
    ZontHeatingCircuitInternalState,
    ZontHeatingModeConfiguration,
    immutable_heating_modes,
    immutable_heating_states,
)
from custom_components.zont_local.protocol.objects import (
    ZontHeatingCircuitData,
    immutable_objects,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

OFF_MODE_ID = 20504
CIRCUIT_ID = 8362


def _coordinator(
    hass: HomeAssistant,
) -> tuple[ZontDataUpdateCoordinator, MockConfigEntry]:
    """Return a coordinator configured with one turn-off mode."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_HEATING_OFF_MODE_ID: OFF_MODE_ID},
    )
    client = MagicMock(spec=ZontClient)
    coordinator = ZontDataUpdateCoordinator(
        hass,
        entry,
        client,
        initial_info=None,
        on_controller_info=MagicMock(),
    )
    return coordinator, entry


def _snapshot(target: int, *, complete: bool = True) -> ZontData:
    """Return one heating snapshot with a configurable mode target."""
    return ZontData(
        controller=ZontControllerData(info=None),
        objects=immutable_objects(
            {
                CIRCUIT_ID: ZontHeatingCircuitData(
                    CIRCUIT_ID,
                    16,
                    "ГВС",
                    subtype=1,
                )
            }
        ),
        heating_states=immutable_heating_states(
            {
                CIRCUIT_ID: ZontHeatingCircuitInternalState(
                    CIRCUIT_ID,
                    target_sensor_id=None,
                    status_register=0,
                )
            }
            if complete
            else None
        ),
        heating_modes=immutable_heating_modes(
            {
                OFF_MODE_ID: ZontHeatingModeConfiguration(
                    OFF_MODE_ID,
                    "Выключен",
                    {CIRCUIT_ID: target},
                )
            }
        ),
    )


def test_invalid_heating_off_mode_issue_requires_complete_snapshot(
    hass: HomeAssistant,
) -> None:
    coordinator, entry = _coordinator(hass)
    issue_registry = ir.async_get(hass)
    issue_id = heating_off_mode_issue_id(entry.entry_id)

    coordinator._async_update_off_mode_issue(_snapshot(30, complete=False))
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    coordinator._async_update_off_mode_issue(_snapshot(30))
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "heating_off_mode_invalid"
    assert issue.severity is ir.IssueSeverity.ERROR
    assert not issue.is_fixable
    assert not issue.is_persistent

    coordinator._async_update_off_mode_issue(_snapshot(0))
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_entry_issue_cleanup_removes_heating_and_export_issues(
    hass: HomeAssistant,
) -> None:
    binding = ZontExportBinding(
        ZontExportKind.TEMPERATURE,
        "source-id",
        4110,
        "HA sensor",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_EXPORTS: [binding.as_dict()]},
    )
    async_set_heating_off_mode_issue(hass, entry.entry_id, invalid=True)
    ir.async_create_issue(
        hass,
        DOMAIN,
        export_issue_id(binding),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="export_target_invalid",
        translation_placeholders={"target": "HA sensor", "target_id": "4110"},
    )

    await async_remove_entry(hass, entry)

    issue_registry = ir.async_get(hass)
    assert (
        issue_registry.async_get_issue(
            DOMAIN,
            heating_off_mode_issue_id(entry.entry_id),
        )
        is None
    )
    assert issue_registry.async_get_issue(DOMAIN, export_issue_id(binding)) is None
