"""Shared fixtures for ZONT WebSocket tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@dataclass
class FakeBus:
    """Collect fired Home Assistant events."""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def async_fire(self, event_type: str, data: dict[str, Any]) -> None:
        """Record an event."""
        self.events.append((event_type, data))


@dataclass
class FakeHass:
    """Provide the small HomeAssistant surface required by client tests."""

    bus: FakeBus = field(default_factory=FakeBus)
    data: dict[str, Any] = field(default_factory=dict)

    def async_create_task(self, coroutine: Any, name: str) -> Any:
        """Create an asyncio task like Home Assistant."""
        import asyncio

        return asyncio.create_task(coroutine, name=name)

    def verify_event_loop_thread(self, action: str) -> None:
        """Accept dispatcher calls made from the running test loop."""


@pytest.fixture
def fake_hass() -> FakeHass:
    """Return a lightweight fake Home Assistant object."""
    return FakeHass()


@pytest.fixture
def auth_error_callback() -> Callable[[], None]:
    """Return a no-op authentication callback."""
    return lambda: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable loading custom integrations in Home Assistant tests."""
