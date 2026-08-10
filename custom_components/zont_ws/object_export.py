"""Export Home Assistant temperature entities to ZONT objects."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util.unit_conversion import TemperatureConverter

from .client import (
    ZontConnectionError,
    ZontProtocolError,
    ZontRequestTimeoutError,
    ZontWsClient,
)
from .const import (
    CONF_EXPORT_SOURCE,
    CONF_EXPORT_TARGET_ID,
    CONF_EXPORT_TARGET_NAME,
    CONF_TEMPERATURE_EXPORTS,
    DOMAIN,
    EXPORT_HEARTBEAT_INTERVAL,
    connection_signal,
)
from .objects import OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR


class ZontExportSourceError(ValueError):
    """Raised when a configured Home Assistant source is invalid."""


class ZontExportSourceUnavailable(ZontExportSourceError):
    """Raised when a valid source has no value to export right now."""


@dataclass(frozen=True, slots=True)
class ZontTemperatureExportBinding:
    """One Home Assistant source bound to one ZONT object."""

    source: str
    target_id: int
    target_name: str

    def as_dict(self) -> dict[str, str | int]:
        """Return the config-entry representation of the binding."""
        return {
            CONF_EXPORT_SOURCE: self.source,
            CONF_EXPORT_TARGET_ID: self.target_id,
            CONF_EXPORT_TARGET_NAME: self.target_name,
        }


def temperature_export_bindings(
    options: Mapping[str, Any],
) -> tuple[ZontTemperatureExportBinding, ...]:
    """Return the valid, unambiguous temperature export bindings."""
    stored = options.get(CONF_TEMPERATURE_EXPORTS, [])
    if not isinstance(stored, list | tuple):
        return ()

    bindings: list[ZontTemperatureExportBinding] = []
    sources: set[str] = set()
    targets: set[int] = set()
    for item in stored:
        if not isinstance(item, Mapping):
            continue
        source = item.get(CONF_EXPORT_SOURCE)
        target_id = item.get(CONF_EXPORT_TARGET_ID)
        target_name = item.get(CONF_EXPORT_TARGET_NAME)
        if (
            not isinstance(source, str)
            or not source.strip()
            or type(target_id) is not int
            or target_id < 0
            or not isinstance(target_name, str)
            or not target_name.strip()
        ):
            continue
        source = source.strip()
        target_name = target_name.strip()
        if source in sources or target_id in targets:
            continue
        sources.add(source)
        targets.add(target_id)
        bindings.append(ZontTemperatureExportBinding(source, target_id, target_name))
    return tuple(bindings)


def temperature_export_target_ids(options: Mapping[str, Any]) -> frozenset[int]:
    """Return object IDs reserved as export targets."""
    return frozenset(
        binding.target_id for binding in temperature_export_bindings(options)
    )


def command_response_id(response: Mapping[str, Any]) -> int | None:
    """Return one unambiguous object ID from a command response."""
    lower = response.get("id")
    upper = response.get("Id")
    if lower is not None and upper is not None and lower != upper:
        return None
    value = lower if lower is not None else upper
    return value if type(value) is int and value >= 0 else None


@callback
def export_source_reference(hass: HomeAssistant, entity_id: str) -> str:
    """Return a rename-safe reference for an entity when possible."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    return registry_entry.id if registry_entry is not None else entity_id


@callback
def resolve_export_source(hass: HomeAssistant, source: str) -> str | None:
    """Resolve a stored entity registry UUID or entity ID."""
    return er.async_resolve_entity_id(er.async_get(hass), source)


def export_temperature_from_state(state: State) -> float:
    """Validate and convert one Home Assistant temperature state to Celsius."""
    if state.domain != "sensor" or state.attributes.get(ATTR_DEVICE_CLASS) != (
        SensorDeviceClass.TEMPERATURE
    ):
        raise ZontExportSourceError("Source is not a temperature sensor")
    if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        raise ZontExportSourceUnavailable("Source has no current temperature")

    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if not isinstance(unit, str) or unit not in TemperatureConverter.VALID_UNITS:
        raise ZontExportSourceError("Source has an unsupported temperature unit")
    try:
        value = float(state.state)
        value = TemperatureConverter.convert(
            value,
            unit,
            UnitOfTemperature.CELSIUS,
        )
    except (TypeError, ValueError, HomeAssistantError) as err:
        raise ZontExportSourceError("Source temperature cannot be converted") from err
    if not isfinite(value):
        raise ZontExportSourceError("Source temperature is not finite")
    rounded = round(value, 1)
    return 0.0 if rounded == 0 else rounded


def export_temperature_command(value: float) -> str:
    """Return the confirmed ZONT command for a Celsius temperature."""
    return f"1 {value:.1f}"


@callback
def validate_export_source(
    hass: HomeAssistant,
    entity_id: str,
    config_entry_id: str,
) -> float:
    """Validate a selectable source and return its Celsius temperature."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is not None and (
        registry_entry.platform == DOMAIN
        or registry_entry.config_entry_id == config_entry_id
    ):
        raise ZontExportSourceError("ZONT entities cannot be export sources")
    state = hass.states.get(entity_id)
    if state is None:
        raise ZontExportSourceError("Source entity does not exist")
    return export_temperature_from_state(state)


class ZontTemperatureExportManager:
    """Synchronize configured Home Assistant temperatures with ZONT."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZontWsClient,
    ) -> None:
        """Initialize the export manager."""
        self.hass = hass
        self._entry = entry
        self._client = client
        self._bindings = temperature_export_bindings(entry.options)
        self._resolved_sources: dict[int, str] = {}
        self._validated_targets: set[int] = set()
        self._active_targets: set[int] = set()
        self._error_targets: set[int] = set()
        self._sync_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._unsubscribe_states: Callable[[], None] | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._started = False
        self._shutdown = False

    @property
    def configured_count(self) -> int:
        """Return the number of configured exports."""
        return len(self._bindings)

    @property
    def active_count(self) -> int:
        """Return the number of exports with a successful last write."""
        return len(self._active_targets)

    @property
    def error_count(self) -> int:
        """Return the number of structurally broken exports."""
        return len(self._error_targets)

    @callback
    def async_start(self) -> None:
        """Start subscriptions and the initial non-blocking synchronization."""
        if self._started or not self._bindings:
            return
        self._started = True
        self._async_subscribe_sources()
        self._unsubscribers.extend(
            (
                async_dispatcher_connect(
                    self.hass,
                    connection_signal(self._entry.entry_id),
                    self._async_connection_changed,
                ),
                self.hass.bus.async_listen(
                    er.EVENT_ENTITY_REGISTRY_UPDATED,
                    self._async_entity_registry_updated,
                ),
                async_track_time_interval(
                    self.hass,
                    self._async_heartbeat,
                    timedelta(seconds=EXPORT_HEARTBEAT_INTERVAL),
                    name=f"{DOMAIN} temperature export heartbeat",
                ),
            )
        )
        if self._client.is_connected:
            self._create_task(
                self._async_sync_all(validate_targets=True),
                "initial temperature export",
            )

    async def async_shutdown(self) -> None:
        """Cancel work and release all subscriptions."""
        if self._shutdown:
            return
        self._shutdown = True
        if self._unsubscribe_states is not None:
            self._unsubscribe_states()
            self._unsubscribe_states = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @callback
    def _async_subscribe_sources(self) -> None:
        """Resolve current entity IDs and replace the state subscription."""
        resolved: dict[int, str] = {}
        for binding in self._bindings:
            entity_id = resolve_export_source(self.hass, binding.source)
            if entity_id is not None:
                resolved[binding.target_id] = entity_id
        if resolved == self._resolved_sources and self._unsubscribe_states is not None:
            return
        if self._unsubscribe_states is not None:
            self._unsubscribe_states()
        self._resolved_sources = resolved
        entity_ids = tuple(sorted(set(resolved.values())))
        self._unsubscribe_states = (
            async_track_state_change_event(
                self.hass,
                entity_ids,
                self._async_source_state_changed,
            )
            if entity_ids
            else None
        )

    @callback
    def _async_source_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Schedule immediate writes for a changed source."""
        entity_id = event.data["entity_id"]
        for binding in self._bindings:
            if self._resolved_sources.get(binding.target_id) == entity_id:
                self._create_task(
                    self._async_sync_binding(binding, validate_target=False),
                    f"temperature export {binding.target_id}",
                )

    @callback
    def _async_entity_registry_updated(self, event: Event) -> None:
        """Follow source entity ID changes through their registry UUIDs."""
        previous = dict(self._resolved_sources)
        self._async_subscribe_sources()
        if previous != self._resolved_sources and self._client.is_connected:
            self._create_task(
                self._async_sync_all(validate_targets=False),
                "renamed temperature export source",
            )

    @callback
    def _async_connection_changed(self, connected: bool) -> None:
        """Revalidate targets and synchronize after a reconnect."""
        if not connected:
            self._validated_targets.clear()
            self._active_targets.clear()
            return
        self._create_task(
            self._async_sync_all(validate_targets=True),
            "reconnected temperature export",
        )

    async def _async_heartbeat(self, _now: Any) -> None:
        """Repeat the latest valid values to keep external sensors alive."""
        await self._async_sync_all(validate_targets=False)

    async def _async_sync_all(self, *, validate_targets: bool) -> None:
        """Synchronize all configured bindings sequentially."""
        if self._shutdown or not self._client.is_connected:
            return
        self._async_subscribe_sources()
        async with self._sync_lock:
            for binding in self._bindings:
                await self._async_sync_binding_locked(
                    binding,
                    validate_target=validate_targets,
                )

    async def _async_sync_binding(
        self,
        binding: ZontTemperatureExportBinding,
        *,
        validate_target: bool,
    ) -> None:
        """Synchronize one binding while serializing export writes."""
        if self._shutdown or not self._client.is_connected:
            return
        async with self._sync_lock:
            await self._async_sync_binding_locked(
                binding,
                validate_target=validate_target,
            )

    async def _async_sync_binding_locked(
        self,
        binding: ZontTemperatureExportBinding,
        *,
        validate_target: bool,
    ) -> None:
        """Validate and write one binding with the synchronization lock held."""
        if (
            validate_target or binding.target_id not in self._validated_targets
        ) and not await self._async_validate_target(binding):
            return

        entity_id = self._resolved_sources.get(binding.target_id)
        if entity_id is None or self.hass.states.get(entity_id) is None:
            self._set_issue(binding, "export_source_missing")
            return
        try:
            value = validate_export_source(
                self.hass,
                entity_id,
                self._entry.entry_id,
            )
        except ZontExportSourceUnavailable:
            self._active_targets.discard(binding.target_id)
            return
        except ZontExportSourceError:
            self._set_issue(binding, "export_source_invalid")
            return

        try:
            response = await self._client.async_send_command(
                binding.target_id,
                export_temperature_command(value),
            )
        except (ZontConnectionError, ZontRequestTimeoutError):
            self._active_targets.discard(binding.target_id)
            return
        except ZontProtocolError:
            self._set_issue(binding, "export_command_rejected")
            return

        if (
            command_response_id(response) != binding.target_id
            or type(response.get("cmdres")) is not int
            or response["cmdres"] != 0
        ):
            self._set_issue(binding, "export_command_rejected")
            return
        self._mark_healthy(binding)

    async def _async_validate_target(
        self, binding: ZontTemperatureExportBinding
    ) -> bool:
        """Confirm that a target ID still identifies a type=1 object."""
        try:
            response = await self._client.async_get_object_state(binding.target_id)
        except (ZontConnectionError, ZontRequestTimeoutError):
            self._active_targets.discard(binding.target_id)
            return False
        except ZontProtocolError:
            self._set_issue(binding, "export_target_invalid")
            return False
        object_type = response.get("type")
        if (
            response.get("failed")
            or type(object_type) is not int
            or object_type != OBJECT_TYPE_DIGITAL_TEMPERATURE_SENSOR
        ):
            self._set_issue(binding, "export_target_invalid")
            return False
        self._validated_targets.add(binding.target_id)
        return True

    @callback
    def _mark_healthy(self, binding: ZontTemperatureExportBinding) -> None:
        """Record a successful write and clear a previous structural issue."""
        self._active_targets.add(binding.target_id)
        self._error_targets.discard(binding.target_id)
        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            self._issue_id(binding.target_id),
        )

    @callback
    def _set_issue(
        self,
        binding: ZontTemperatureExportBinding,
        translation_key: str,
    ) -> None:
        """Pause one broken binding and expose a single repair issue."""
        self._validated_targets.discard(binding.target_id)
        self._active_targets.discard(binding.target_id)
        self._error_targets.add(binding.target_id)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(binding.target_id),
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=translation_key,
            translation_placeholders={
                "target": binding.target_name,
                "target_id": str(binding.target_id),
            },
        )

    @staticmethod
    def _issue_id(target_id: int) -> str:
        """Return one stable Repairs identifier per export target."""
        return f"temperature_export_{target_id}"

    @callback
    def _create_task(self, coroutine: Any, name: str) -> None:
        """Create and track one config-entry-owned background task."""
        if self._shutdown:
            coroutine.close()
            return
        task = self._entry.async_create_background_task(
            self.hass,
            coroutine,
            f"{DOMAIN} {name}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
