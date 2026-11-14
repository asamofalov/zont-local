"""Expose test helpers for the current and minimum supported Home Assistant."""

from importlib.util import find_spec

if find_spec("pytest_homeassistant") is not None:
    from pytest_homeassistant.common import MockConfigEntry, async_capture_events
else:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_capture_events,
    )

__all__ = ("MockConfigEntry", "async_capture_events")
