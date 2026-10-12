"""Readings of the dependency inputs at the time of a calculation.

The handler takes snapshots of Home Assistant states; this module turns them
into readings without touching Home Assistant. A neighbour is a Versatile
Thermostat climate entity (temperature, availability, safety mode, window
state) or a temperature sensor (temperature only). Door sensors report an open
connection when any of them is ``on``; an unknown or unavailable sensor counts
as closed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .dependencies import MODE_ALWAYS, MODE_DOOR, DependencyItem

STATE_ON = "on"
_UNAVAILABLE_STATES = {None, "unavailable", "unknown"}
CLIMATE_DOMAIN = "climate"


@dataclass(frozen=True)
class EntitySnapshot:
    """What Home Assistant knows about one entity at the time of a calculation."""

    entity_id: str | None
    state: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str | None:
        return self.entity_id.split(".", 1)[0] if self.entity_id else None


@dataclass(frozen=True)
class NeighbourReading:
    """State of one neighbour as used by the zone model."""

    item_id: str
    entity_id: str | None
    temperature: float | None
    available: bool
    is_thermostat: bool
    safety: bool = False
    window_open: bool = False

    @property
    def usable(self) -> bool:
        """True when the neighbour may influence the model."""
        return self.available and not self.safety and self.temperature is not None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nested_on(attributes: Mapping[str, Any], block: str, *keys: str) -> bool:
    nested = attributes.get(block)
    if not isinstance(nested, Mapping):
        return False
    return any(nested.get(key) == STATE_ON for key in keys)


def read_neighbour(item: DependencyItem, snapshot: EntitySnapshot | None) -> NeighbourReading:
    """Return the reading of a neighbour item from its entity snapshot."""
    if snapshot is None or snapshot.entity_id is None or snapshot.state in _UNAVAILABLE_STATES:
        return NeighbourReading(
            item_id=item.item_id,
            entity_id=None if snapshot is None else snapshot.entity_id,
            temperature=None,
            available=False,
            is_thermostat=snapshot is not None and snapshot.domain == CLIMATE_DOMAIN,
        )
    if snapshot.domain == CLIMATE_DOMAIN:
        attributes = snapshot.attributes
        return NeighbourReading(
            item_id=item.item_id,
            entity_id=snapshot.entity_id,
            temperature=_finite(attributes.get("current_temperature")),
            available=True,
            is_thermostat=True,
            safety=_nested_on(attributes, "safety_manager", "safety_state"),
            window_open=_nested_on(attributes, "window_manager", "window_state", "window_auto_state"),
        )
    return NeighbourReading(
        item_id=item.item_id,
        entity_id=snapshot.entity_id,
        temperature=_finite(snapshot.state),
        available=True,
        is_thermostat=False,
    )


def doors_open(snapshots: Iterable[EntitySnapshot | None]) -> bool:
    """Return True when any door sensor reports open."""
    return any(snapshot is not None and snapshot.state == STATE_ON for snapshot in snapshots)


def connection_active(item: DependencyItem, reading: NeighbourReading, door_open: bool) -> bool:
    """Return whether the neighbour currently exchanges heat with the zone."""
    if not reading.usable:
        return False
    if item.mode == MODE_ALWAYS:
        return True
    if item.mode == MODE_DOOR:
        return door_open
    return False


@dataclass(frozen=True)
class NeighbourObservation:
    """One neighbour item with its reading and the state of its doors."""

    item: DependencyItem
    reading: NeighbourReading
    door_open: bool = False

    @property
    def connected(self) -> bool:
        return connection_active(self.item, self.reading, self.door_open)


@dataclass(frozen=True)
class DependencyReadings:
    """Everything the dependency layer read for one calculation."""

    neighbours: tuple[NeighbourObservation, ...] = ()
    # Preset temperatures of the zone thermostat, e.g. {"frost_temp": 7.0, "eco_temp": 17.0}.
    presets: Mapping[str, float] = field(default_factory=dict)
