"""Repair issue lifecycle for ZONT Local."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .export.model import export_bindings, export_issue_id

_HEATING_OFF_MODE_ISSUE = "heating_off_mode_invalid"


def heating_off_mode_issue_id(entry_id: str) -> str:
    """Return the config-entry-scoped heating-mode issue identifier."""
    return f"{_HEATING_OFF_MODE_ISSUE}_{entry_id}"


@callback
def async_set_heating_off_mode_issue(
    hass: HomeAssistant,
    entry_id: str,
    *,
    invalid: bool,
) -> None:
    """Create or clear the issue for an invalid configured off mode."""
    issue_id = heating_off_mode_issue_id(entry_id)
    if not invalid:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=_HEATING_OFF_MODE_ISSUE,
    )


@callback
def async_delete_entry_issues(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Delete every Repair issue owned by a removed config entry."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        heating_off_mode_issue_id(entry.entry_id),
    )
    for binding in export_bindings(entry.options):
        ir.async_delete_issue(hass, DOMAIN, export_issue_id(binding))
