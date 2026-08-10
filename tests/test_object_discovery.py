"""Tests for bounded object discovery in configuration flows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from custom_components.zont_ws.client import ZontProtocolError
from custom_components.zont_ws.object_discovery import (
    ZontObjectDiscoveryError,
    async_discover_importable_objects_from_requests,
)
from custom_components.zont_ws.objects import (
    OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
    OBJECT_TYPE_RADIO_SENSOR,
    SUPPORTED_OBJECT_TYPES,
    ZontDigitalTemperatureSensorData,
)


async def test_discovery_returns_only_importable_objects() -> None:
    requests = AsyncMock()

    async def get_ids(object_type: int) -> list[int]:
        if object_type == OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR:
            return [8196, 8197]
        if object_type == OBJECT_TYPE_RADIO_SENSOR:
            return [9000]
        return []

    async def get_state(object_id: int) -> dict[str, object]:
        if object_id == 8196:
            return {
                "id": 8196,
                "type": OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR,
                "name": "Улица",
                "t": -5.2,
                "a": 1,
            }
        if object_id == 8197:
            return {"id": 8197, "req_state": 0, "failed": 1}
        return {
            "id": 9000,
            "type": OBJECT_TYPE_RADIO_SENSOR,
            "stype": 17,
            "name": "Неподдерживаемая розетка",
            "a": 1,
        }

    requests.async_get_object_ids.side_effect = get_ids
    requests.async_get_object_state.side_effect = get_state

    objects = await async_discover_importable_objects_from_requests(requests)

    assert list(objects) == [8196]
    assert isinstance(objects[8196], ZontDigitalTemperatureSensorData)
    assert requests.async_get_object_ids.await_count == len(SUPPORTED_OBJECT_TYPES)


async def test_discovery_fails_when_a_type_list_is_incomplete() -> None:
    requests = AsyncMock()
    requests.async_get_object_ids.side_effect = ZontProtocolError("bad ids")

    with pytest.raises(ZontObjectDiscoveryError):
        await async_discover_importable_objects_from_requests(requests)
