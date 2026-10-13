"""Tests for the zone dependency data model, parsing, validation and per-item state."""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.dependencies import (
    ERROR_CUSTOM_EMITTER_WITHOUT_RESPONSE,
    ERROR_DOOR_MODE_WITHOUT_SENSOR,
    ERROR_DUPLICATE_ID,
    ERROR_DUPLICATE_NEIGHBOUR,
    ERROR_MISSING_ENTITY,
    ERROR_MISSING_ID,
    ERROR_INVALID_SPLIT_PARAMETER,
    ERROR_SECOND_SOURCE,
    ERROR_SELF_REFERENCE,
    ERROR_SHARED_DOOR_SENSOR,
    ERROR_SOURCE_IS_NEIGHBOUR,
    ERROR_UNKNOWN_EMITTER,
    ERROR_UNKNOWN_KIND,
    ERROR_UNKNOWN_MODE,
    ERROR_UNKNOWN_SPLIT_RULE,
    ERROR_UNKNOWN_WINDOW_ACTION,
    KIND_NEIGHBOUR,
    KIND_SOURCE,
    MODE_ALWAYS,
    MODE_DOOR,
    DependencyState,
    dump_dependencies,
    parse_dependencies,
    validate_dependencies,
)


def _neighbour(item_id="n1", entity="reg-kitchen", mode=MODE_ALWAYS, sensors=(), action="nothing"):
    return {
        "id": item_id,
        "kind": KIND_NEIGHBOUR,
        "entity": entity,
        "mode": mode,
        "door_sensors": list(sensors),
        "neighbour_window_action": action,
    }


def _source(item_id="s1", entity="reg-floor", **fields):
    values = {
        "id": item_id,
        "kind": KIND_SOURCE,
        "entity": entity,
        "split_rule": "lead_band",
        "lead_band_percent": 20,
        "lead_emitter": "radiator",
        "source_emitter": "floor_water",
    }
    values.update(fields)
    return values


def _codes(raw, own_entity="reg-own"):
    return [(issue.code, issue.item_id) for issue in validate_dependencies(parse_dependencies(raw), own_entity)]


def test_parse_round_trips_through_the_stored_options() -> None:
    raw = [
        _neighbour(sensors=("reg-door",), mode=MODE_DOOR),
        {"id": "s1", "kind": KIND_SOURCE, "entity": "reg-floor", "split_rule": "proportional"},
    ]

    items = parse_dependencies(raw)

    assert [item.kind for item in items] == [KIND_NEIGHBOUR, KIND_SOURCE]
    assert items[0].door_sensors == ("reg-door",)
    assert items[1].extra == {"split_rule": "proportional"}
    assert dump_dependencies(items) == raw


@pytest.mark.parametrize("raw", [None, "neighbour", {"id": "x"}, 3])
def test_parse_ignores_a_malformed_option(raw) -> None:
    assert parse_dependencies(raw) == []


@pytest.mark.parametrize("sensors", [5, {"a": 1}, None])
def test_parse_ignores_malformed_door_sensors(sensors) -> None:
    (item,) = parse_dependencies([dict(_neighbour(), door_sensors=sensors)])

    assert item.door_sensors == ()


def test_state_without_content_is_empty() -> None:
    state = DependencyState()
    state.for_item("n1")

    assert not state


def test_empty_list_is_valid() -> None:
    assert _codes([]) == []


def test_valid_neighbours_and_one_source() -> None:
    raw = [
        _neighbour("n1", "reg-kitchen", MODE_DOOR, ("reg-door-1",)),
        _neighbour("n2", "reg-hall", MODE_ALWAYS),
        _source(),
    ]
    assert _codes(raw) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([_neighbour(item_id="")], [(ERROR_MISSING_ID, None)]),
        ([_neighbour("n1", "reg-a"), _neighbour("n1", "reg-b")], [(ERROR_DUPLICATE_ID, "n1")]),
        ([{"id": "x", "kind": "pipe", "entity": "reg-a"}], [(ERROR_UNKNOWN_KIND, "x")]),
        ([_neighbour(entity="")], [(ERROR_MISSING_ENTITY, "n1")]),
        ([_neighbour(entity="reg-own")], [(ERROR_SELF_REFERENCE, "n1")]),
        ([_neighbour("n1", "reg-a"), _neighbour("n2", "reg-a")], [(ERROR_DUPLICATE_NEIGHBOUR, "n2")]),
        ([_neighbour(mode="sometimes")], [(ERROR_UNKNOWN_MODE, "n1")]),
        ([_neighbour(mode=MODE_DOOR)], [(ERROR_DOOR_MODE_WITHOUT_SENSOR, "n1")]),
        ([_neighbour(action="open_the_window")], [(ERROR_UNKNOWN_WINDOW_ACTION, "n1")]),
        (
            [_neighbour("n1", "reg-a", MODE_DOOR, ("reg-door",)), _neighbour("n2", "reg-b", MODE_DOOR, ("reg-door",))],
            [(ERROR_SHARED_DOOR_SENSOR, "n2")],
        ),
        (
            [_source("s1", "reg-a"), _source("s2", "reg-b")],
            [(ERROR_SECOND_SOURCE, "s2")],
        ),
        ([_source(entity="reg-a"), _neighbour("n1", "reg-a")], [(ERROR_SOURCE_IS_NEIGHBOUR, "n1")]),
        ([_source(split_rule="half_half")], [(ERROR_UNKNOWN_SPLIT_RULE, "s1")]),
        ([_source(split_ratio=0)], [(ERROR_INVALID_SPLIT_PARAMETER, "s1")]),
        ([_source(source_alone_percent=120)], [(ERROR_INVALID_SPLIT_PARAMETER, "s1")]),
        ([_source(source_emitter="stove")], [(ERROR_UNKNOWN_EMITTER, "s1")]),
        ([_source(lead_emitter="custom", lead_lag_min=5)], [(ERROR_CUSTOM_EMITTER_WITHOUT_RESPONSE, "s1")]),
    ],
    ids=[
        "missing_id",
        "duplicate_id",
        "unknown_kind",
        "missing_entity",
        "self_reference",
        "duplicate_neighbour",
        "unknown_mode",
        "door_mode_without_sensor",
        "unknown_window_action",
        "shared_door_sensor",
        "second_source",
        "source_is_neighbour",
        "unknown_split_rule",
        "invalid_ratio",
        "invalid_alone",
        "unknown_emitter",
        "custom_emitter_without_response",
    ],
)
def test_validation_reports_structural_problems(raw, expected) -> None:
    assert _codes(raw) == expected


def test_none_mode_keeps_the_item_without_sensors() -> None:
    """A neighbour switched to 'none' stays on the list as an off switch."""
    assert _codes([_neighbour(mode="none")]) == []


def test_state_round_trip_prune_and_reset() -> None:
    state = DependencyState()
    state.for_item("n1")["k"] = 0.004
    state.for_item("n2")["k"] = 0.002

    restored = DependencyState()
    restored.load_state(state.save_state())
    assert restored.items == {"n1": {"k": 0.004}, "n2": {"k": 0.002}}

    assert restored.prune(["n1"]) == ["n2"]
    assert restored.items == {"n1": {"k": 0.004}}

    restored.reset()
    assert not restored


@pytest.mark.parametrize("saved", [None, {}, {"version": 99, "items": {"n1": {}}}, {"version": 1, "items": []}])
def test_state_ignores_an_unknown_format(saved) -> None:
    state = DependencyState()
    state.for_item("n1")["k"] = 1.0

    state.load_state(saved)

    assert not state
