"""Tests for config-entry-owned ZONT runtime resources."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.zont_local.coordinator import ZontDataUpdateCoordinator
from custom_components.zont_local.export import ZontExportManager
from custom_components.zont_local.object_platform import ZontObjectEntityManager
from custom_components.zont_local.protocol import ZontClient
from custom_components.zont_local.runtime import ZontRuntimeData


def _shutdown_mock(spec: type, name: str, calls: list[str]) -> MagicMock:
    resource = MagicMock(spec=spec)
    resource.async_shutdown = AsyncMock(side_effect=lambda: calls.append(name))
    return resource


async def test_shutdown_releases_resources_in_dependency_order() -> None:
    calls: list[str] = []
    client = MagicMock(spec=ZontClient)
    client.async_stop = AsyncMock(side_effect=lambda: calls.append("client"))
    coordinator = _shutdown_mock(ZontDataUpdateCoordinator, "coordinator", calls)
    export_manager = _shutdown_mock(
        ZontExportManager,
        "export_manager",
        calls,
    )
    object_entities = _shutdown_mock(
        ZontObjectEntityManager,
        "object_entities",
        calls,
    )
    runtime = ZontRuntimeData(
        client,
        coordinator,
        export_manager,
        object_entities=object_entities,
    )

    await runtime.async_shutdown()

    assert calls == ["object_entities", "export_manager", "coordinator", "client"]


async def test_shutdown_continues_after_an_earlier_resource_fails() -> None:
    calls: list[str] = []
    client = MagicMock(spec=ZontClient)
    client.async_stop = AsyncMock(side_effect=lambda: calls.append("client"))
    coordinator = _shutdown_mock(ZontDataUpdateCoordinator, "coordinator", calls)
    export_manager = _shutdown_mock(
        ZontExportManager,
        "export_manager",
        calls,
    )
    object_entities = MagicMock(spec=ZontObjectEntityManager)

    async def fail_object_entities() -> None:
        calls.append("object_entities")
        raise RuntimeError("entity manager failed")

    object_entities.async_shutdown = AsyncMock(side_effect=fail_object_entities)
    runtime = ZontRuntimeData(
        client,
        coordinator,
        export_manager,
        object_entities=object_entities,
    )

    with pytest.raises(RuntimeError, match="entity manager failed"):
        await runtime.async_shutdown()

    assert calls == ["object_entities", "export_manager", "coordinator", "client"]
