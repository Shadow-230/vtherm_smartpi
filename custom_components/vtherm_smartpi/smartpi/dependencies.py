"""Zone dependencies configured on a SmartPI thermostat entry.

A dependency is either a neighbouring zone that exchanges heat with this zone or
a second heat source in this zone. Each item refers to one Home Assistant
entity by its entity registry id, so renaming the entity id keeps the link.
This module holds the data model, the parsing of the stored options, the
structural validation and the learned state kept per item.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .source_model import EMITTER_CUSTOM, EMITTERS
from .split import RULES

KIND_NEIGHBOUR = "neighbour"
KIND_SOURCE = "source"
KINDS = (KIND_NEIGHBOUR, KIND_SOURCE)

MODE_NONE = "none"
MODE_DOOR = "door"
MODE_ALWAYS = "always"
NEIGHBOUR_MODES = (MODE_NONE, MODE_DOOR, MODE_ALWAYS)

WINDOW_ACTION_NOTHING = "nothing"
WINDOW_ACTIONS = (WINDOW_ACTION_NOTHING, "turn_off", "frost", "eco")

KEY_ID = "id"
KEY_KIND = "kind"
KEY_ENTITY = "entity"
KEY_MODE = "mode"
KEY_DOOR_SENSORS = "door_sensors"
KEY_NEIGHBOUR_WINDOW_ACTION = "neighbour_window_action"
# Second source: the rule and its parameters (percent), and both emitters.
KEY_SPLIT_RULE = "split_rule"
KEY_SPLIT_RATIO = "split_ratio"
KEY_SOURCE_ALONE = "source_alone_percent"
KEY_LEAD_BAND = "lead_band_percent"
KEY_LEAD_EMITTER = "lead_emitter"
KEY_LEAD_LAG_MIN = "lead_lag_min"
KEY_LEAD_TAU_MIN = "lead_tau_min"
KEY_SOURCE_EMITTER = "source_emitter"
KEY_SOURCE_LAG_MIN = "source_lag_min"
KEY_SOURCE_TAU_MIN = "source_tau_min"

ERROR_MISSING_ID = "missing_id"
ERROR_DUPLICATE_ID = "duplicate_id"
ERROR_UNKNOWN_KIND = "unknown_kind"
ERROR_MISSING_ENTITY = "missing_entity"
ERROR_SELF_REFERENCE = "self_reference"
ERROR_DUPLICATE_NEIGHBOUR = "duplicate_neighbour"
ERROR_UNKNOWN_MODE = "unknown_mode"
ERROR_DOOR_MODE_WITHOUT_SENSOR = "door_mode_without_sensor"
ERROR_SHARED_DOOR_SENSOR = "shared_door_sensor"
ERROR_UNKNOWN_WINDOW_ACTION = "unknown_window_action"
ERROR_SECOND_SOURCE = "second_source"
ERROR_SOURCE_IS_NEIGHBOUR = "source_is_neighbour"
ERROR_UNKNOWN_SPLIT_RULE = "unknown_split_rule"
ERROR_INVALID_SPLIT_PARAMETER = "invalid_split_parameter"
ERROR_UNKNOWN_EMITTER = "unknown_emitter"
ERROR_CUSTOM_EMITTER_WITHOUT_RESPONSE = "custom_emitter_without_response"

DEPENDENCY_STATE_VERSION = 1


@dataclass(frozen=True)
class DependencyItem:
    """One configured dependency of the zone."""

    item_id: str
    kind: str
    entity: str
    mode: str = MODE_NONE
    door_sensors: tuple[str, ...] = ()
    neighbour_window_action: str = WINDOW_ACTION_NOTHING
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def is_neighbour(self) -> bool:
        return self.kind == KIND_NEIGHBOUR

    @property
    def is_source(self) -> bool:
        return self.kind == KIND_SOURCE


@dataclass(frozen=True)
class ValidationIssue:
    """A configuration problem attached to one item (or to the list when item_id is None)."""

    code: str
    item_id: str | None = None


_KNOWN_KEYS = {
    KEY_ID,
    KEY_KIND,
    KEY_ENTITY,
    KEY_MODE,
    KEY_DOOR_SENSORS,
    KEY_NEIGHBOUR_WINDOW_ACTION,
}


def parse_dependencies(raw: Any) -> list[DependencyItem]:
    """Read the stored option list; malformed entries are kept for validation, not dropped."""
    if not isinstance(raw, (list, tuple)):
        return []
    items: list[DependencyItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sensors = entry.get(KEY_DOOR_SENSORS) or ()
        if isinstance(sensors, str):
            sensors = (sensors,)
        elif not isinstance(sensors, (list, tuple)):
            sensors = ()
        items.append(
            DependencyItem(
                item_id=str(entry.get(KEY_ID) or ""),
                kind=str(entry.get(KEY_KIND) or ""),
                entity=str(entry.get(KEY_ENTITY) or ""),
                mode=str(entry.get(KEY_MODE) or MODE_NONE),
                door_sensors=tuple(str(sensor) for sensor in sensors if sensor),
                neighbour_window_action=str(
                    entry.get(KEY_NEIGHBOUR_WINDOW_ACTION) or WINDOW_ACTION_NOTHING
                ),
                extra={key: value for key, value in entry.items() if key not in _KNOWN_KEYS},
            )
        )
    return items


def dump_dependencies(items: Iterable[DependencyItem]) -> list[dict[str, Any]]:
    """Return the option list stored for the given items."""
    stored = []
    for item in items:
        entry: dict[str, Any] = {
            KEY_ID: item.item_id,
            KEY_KIND: item.kind,
            KEY_ENTITY: item.entity,
        }
        if item.is_neighbour:
            entry[KEY_MODE] = item.mode
            entry[KEY_DOOR_SENSORS] = list(item.door_sensors)
            entry[KEY_NEIGHBOUR_WINDOW_ACTION] = item.neighbour_window_action
        entry.update(item.extra)
        stored.append(entry)
    return stored


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _source_issues(item: DependencyItem) -> list[str]:
    """Problems of a source item's rule and emitters."""
    extra = item.extra
    issues = []
    if extra.get(KEY_SPLIT_RULE) not in RULES:
        issues.append(ERROR_UNKNOWN_SPLIT_RULE)
    ratio = _number(extra.get(KEY_SPLIT_RATIO, 100))
    alone = _number(extra.get(KEY_SOURCE_ALONE, 50))
    band = _number(extra.get(KEY_LEAD_BAND, 20))
    if ratio is None or ratio <= 0.0 or alone is None or not 0.0 <= alone <= 100.0:
        issues.append(ERROR_INVALID_SPLIT_PARAMETER)
    elif band is None or not 0.0 <= band <= 100.0:
        issues.append(ERROR_INVALID_SPLIT_PARAMETER)
    for emitter_key, lag_key, tau_key in (
        (KEY_LEAD_EMITTER, KEY_LEAD_LAG_MIN, KEY_LEAD_TAU_MIN),
        (KEY_SOURCE_EMITTER, KEY_SOURCE_LAG_MIN, KEY_SOURCE_TAU_MIN),
    ):
        emitter = extra.get(emitter_key)
        if emitter not in EMITTERS:
            issues.append(ERROR_UNKNOWN_EMITTER)
        elif emitter == EMITTER_CUSTOM:
            lag, tau = _number(extra.get(lag_key)), _number(extra.get(tau_key))
            if lag is None or tau is None or lag < 0.0 or tau < 0.0:
                issues.append(ERROR_CUSTOM_EMITTER_WITHOUT_RESPONSE)
    return issues


def validate_dependencies(
    items: Iterable[DependencyItem], own_entity: str | None = None
) -> list[ValidationIssue]:
    """Return the structural problems of a dependency list.

    ``own_entity`` is the registry id of the thermostat the list belongs to.
    Checks that need Home Assistant (device classes, the algorithm of a source
    thermostat, its other roles) are done where the registry is available.
    """
    issues: list[ValidationIssue] = []
    seen_ids: set[str] = set()
    neighbour_entities: set[str] = set()
    door_sensor_owner: dict[str, str] = {}
    sources = 0
    items = list(items)
    source_entities = {item.entity for item in items if item.is_source and item.entity}

    for item in items:
        if not item.item_id:
            issues.append(ValidationIssue(ERROR_MISSING_ID))
        elif item.item_id in seen_ids:
            issues.append(ValidationIssue(ERROR_DUPLICATE_ID, item.item_id))
        seen_ids.add(item.item_id)

        if item.kind not in KINDS:
            issues.append(ValidationIssue(ERROR_UNKNOWN_KIND, item.item_id))
            continue
        if not item.entity:
            issues.append(ValidationIssue(ERROR_MISSING_ENTITY, item.item_id))
        elif own_entity is not None and item.entity == own_entity:
            issues.append(ValidationIssue(ERROR_SELF_REFERENCE, item.item_id))

        if item.is_source:
            sources += 1
            if sources > 1:
                issues.append(ValidationIssue(ERROR_SECOND_SOURCE, item.item_id))
            issues.extend(ValidationIssue(code, item.item_id) for code in _source_issues(item))
            continue

        if item.entity and item.entity in source_entities:
            issues.append(ValidationIssue(ERROR_SOURCE_IS_NEIGHBOUR, item.item_id))
        if item.entity:
            if item.entity in neighbour_entities:
                issues.append(ValidationIssue(ERROR_DUPLICATE_NEIGHBOUR, item.item_id))
            neighbour_entities.add(item.entity)
        if item.mode not in NEIGHBOUR_MODES:
            issues.append(ValidationIssue(ERROR_UNKNOWN_MODE, item.item_id))
        if item.mode == MODE_DOOR and not item.door_sensors:
            issues.append(ValidationIssue(ERROR_DOOR_MODE_WITHOUT_SENSOR, item.item_id))
        if item.neighbour_window_action not in WINDOW_ACTIONS:
            issues.append(ValidationIssue(ERROR_UNKNOWN_WINDOW_ACTION, item.item_id))
        for sensor in item.door_sensors:
            owner = door_sensor_owner.setdefault(sensor, item.item_id)
            if owner != item.item_id:
                issues.append(ValidationIssue(ERROR_SHARED_DOOR_SENSOR, item.item_id))

    return issues


class DependencyState:
    """Learned state kept per dependency item, persisted with the algorithm state."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        # State learned across items (e.g. simultaneous connections).
        self.shared: dict[str, Any] = {}

    def __bool__(self) -> bool:
        return any(self.items.values()) or bool(self.shared)

    def for_item(self, item_id: str) -> dict[str, Any]:
        """Return the mutable state of one item, creating it on first use."""
        return self.items.setdefault(item_id, {})

    def prune(self, configured_ids: Iterable[str]) -> list[str]:
        """Drop the state of items that are no longer configured; return their ids."""
        keep = set(configured_ids)
        removed = [item_id for item_id in self.items if item_id not in keep]
        for item_id in removed:
            del self.items[item_id]
        return removed

    def reset(self) -> None:
        self.items.clear()
        self.shared.clear()

    def save_state(self) -> dict[str, Any]:
        return {
            "version": DEPENDENCY_STATE_VERSION,
            "items": {k: dict(v) for k, v in self.items.items()},
            "shared": dict(self.shared),
        }

    def load_state(self, state: Any) -> None:
        """Restore a saved state; an unknown format is ignored rather than guessed."""
        self.items = {}
        self.shared = {}
        if not isinstance(state, dict) or state.get("version") != DEPENDENCY_STATE_VERSION:
            return
        items = state.get("items")
        if isinstance(items, dict):
            self.items = {str(k): dict(v) for k, v in items.items() if isinstance(v, dict)}
        shared = state.get("shared")
        if isinstance(shared, dict):
            self.shared = dict(shared)
