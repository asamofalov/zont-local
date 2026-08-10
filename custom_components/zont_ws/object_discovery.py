"""Bounded object discovery used by ZONT configuration flows."""

from __future__ import annotations

import asyncio
import logging
from types import MappingProxyType

from .client import (
    ZontProtocolError,
    ZontRequestTimeoutError,
    ZontTemporaryRequestSession,
)
from .object_import import importable_object_descriptor
from .objects import (
    SUPPORTED_OBJECT_TYPES,
    ZontObject,
    ZontObjectParseError,
    parse_zont_object,
)

_LOGGER = logging.getLogger(__name__)
_DISCOVERY_TIMEOUT = 30.0


class ZontObjectDiscoveryError(ZontProtocolError):
    """Raised when a complete object list cannot be discovered safely."""


async def async_discover_importable_objects_from_requests(
    requests: ZontTemporaryRequestSession,
) -> MappingProxyType[int, ZontObject]:
    """Discover public objects through an already authenticated connection."""
    objects: dict[int, ZontObject] = {}
    try:
        async with asyncio.timeout(_DISCOVERY_TIMEOUT):
            for object_type in SUPPORTED_OBJECT_TYPES:
                object_ids = await requests.async_get_object_ids(object_type)
                for object_id in object_ids:
                    payload = await requests.async_get_object_state(object_id)
                    if payload.get("failed"):
                        continue
                    try:
                        obj = parse_zont_object(payload)
                        if obj.object_type != object_type:
                            raise ZontObjectParseError(
                                "Object type does not match requested type"
                            )
                    except ZontObjectParseError:
                        _LOGGER.debug(
                            "Unable to use ZONT object %s during configuration",
                            object_id,
                            exc_info=True,
                        )
                        continue
                    if importable_object_descriptor(obj) is not None:
                        objects[object_id] = obj
    except (TimeoutError, ZontRequestTimeoutError) as err:
        raise ZontObjectDiscoveryError("Object discovery timed out") from err
    except ZontProtocolError as err:
        raise ZontObjectDiscoveryError("Object discovery failed") from err
    return MappingProxyType(objects)
