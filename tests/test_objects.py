"""Tests for typed ZONT object models."""

from __future__ import annotations

import pytest
from custom_components.zont_ws.objects import (
    OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
    ZontDigitalBusAdapterData,
    ZontDigitalBusState,
    ZontObjectParseError,
    immutable_objects,
    parse_digital_bus_adapter,
    unavailable_object,
)


def test_parse_complete_digital_bus_adapter() -> None:
    adapter = parse_digital_bus_adapter(
        {
            "id": 4097,
            "type": OBJECT_TYPE_DIGITAL_BUS_ADAPTER,
            "name": "Navien",
            "water": 45.6,
            "dhw": 34.5,
            "return": 30.4,
            "modul": 99,
            "press": 2.4,
            "state": 1,
            "err": 0,
        }
    )

    assert adapter == ZontDigitalBusAdapterData(
        object_id=4097,
        object_type=6,
        name="Navien",
        flow_temperature=45.6,
        dhw_temperature=34.5,
        return_temperature=30.4,
        modulation=99.0,
        pressure=2.4,
        state=ZontDigitalBusState.RUNNING,
        error_code=0,
    )


def test_partial_update_preserves_absent_fields() -> None:
    previous = parse_digital_bus_adapter(
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": 35,
            "dhw": 29,
            "state": 0,
            "err": 0,
        }
    )

    adapter = parse_digital_bus_adapter(
        {"id": 4097, "water": 36.5},
        previous,
        partial=True,
    )

    assert adapter.flow_temperature == 36.5
    assert adapter.dhw_temperature == 29.0
    assert adapter.state is ZontDigitalBusState.OFF
    assert adapter.error_code == 0


def test_invalid_optional_fields_do_not_break_adapter() -> None:
    adapter = parse_digital_bus_adapter(
        {
            "id": 4097,
            "type": 6,
            "name": "Navien",
            "water": True,
            "dhw": float("nan"),
            "state": [],
            "err": 1.5,
        }
    )

    assert adapter.flow_temperature is None
    assert adapter.dhw_temperature is None
    assert adapter.state is None
    assert adapter.error_code is None


@pytest.mark.parametrize(
    "payload",
    [
        {"type": 6, "name": "Navien"},
        {"id": 4097, "type": 1, "name": "Navien"},
        {"id": 4097, "type": 6, "name": ""},
    ],
)
def test_invalid_identity_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ZontObjectParseError):
        parse_digital_bus_adapter(payload)


def test_object_registry_and_unavailable_copy_are_immutable() -> None:
    adapter = ZontDigitalBusAdapterData(4097, 6, "Navien")
    objects = immutable_objects({4097: adapter})

    assert not unavailable_object(adapter).available
    with pytest.raises(TypeError):
        objects[4098] = adapter  # type: ignore[index]
