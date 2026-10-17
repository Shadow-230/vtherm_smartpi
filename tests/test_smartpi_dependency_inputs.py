"""Tests for reading neighbour and door inputs from Home Assistant state snapshots."""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.dependencies import (
    MODE_ALWAYS,
    MODE_DOOR,
    MODE_NONE,
    DependencyItem,
)
from custom_components.vtherm_smartpi.smartpi.dependency_inputs import (
    EntitySnapshot,
    connection_active,
    doors_open,
    read_neighbour,
)

ITEM = DependencyItem(item_id="n1", kind="neighbour", entity="reg-kitchen", mode=MODE_ALWAYS)


def _vtherm(temperature=21.0, state="heat", safety="off", window="off", window_auto="off"):
    return EntitySnapshot(
        "climate.kitchen",
        state,
        {
            "current_temperature": temperature,
            "safety_manager": {"safety_state": safety},
            "window_manager": {"window_state": window, "window_auto_state": window_auto},
        },
    )


def test_vtherm_neighbour_gives_temperature_safety_and_window() -> None:
    reading = read_neighbour(ITEM, _vtherm(window_auto="on"))

    assert reading.temperature == 21.0
    assert reading.is_thermostat
    assert reading.window_open
    assert not reading.safety
    assert reading.usable


def test_vtherm_neighbour_switched_off_still_has_a_temperature() -> None:
    """A neighbour that does not heat still exchanges heat through its temperature."""
    assert read_neighbour(ITEM, _vtherm(state="off")).usable


def test_temperature_sensor_neighbour_gives_temperature_only() -> None:
    reading = read_neighbour(ITEM, EntitySnapshot("sensor.hall_temperature", "19.4"))

    assert reading.temperature == 19.4
    assert not reading.is_thermostat
    assert not reading.window_open
    assert reading.usable


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        EntitySnapshot(None),
        EntitySnapshot("sensor.hall_temperature", "unavailable"),
        EntitySnapshot("climate.kitchen", "unknown"),
    ],
    ids=["no_snapshot", "unknown_registry_id", "unavailable", "unknown"],
)
def test_unavailable_neighbour_has_no_influence(snapshot) -> None:
    reading = read_neighbour(ITEM, snapshot)

    assert not reading.available
    assert not reading.usable
    assert not connection_active(ITEM, reading, door_open=True)


def test_neighbour_in_safety_mode_has_no_influence() -> None:
    reading = read_neighbour(ITEM, _vtherm(safety="on"))

    assert reading.safety
    assert not reading.usable


@pytest.mark.parametrize("block", [None, "off"], ids=["missing", "not_a_mapping"])
def test_neighbour_without_safety_and_window_blocks_is_usable(block) -> None:
    """A climate neighbour that publishes no safety or window block reads as neither in safety nor open."""
    attributes = {"current_temperature": 21.0}
    if block is not None:
        attributes |= {"safety_manager": block, "window_manager": block}
    reading = read_neighbour(ITEM, EntitySnapshot("climate.kitchen", "heat", attributes))

    assert not reading.safety
    assert not reading.window_open
    assert reading.usable


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "warm", None])
def test_non_finite_temperatures_are_rejected(value) -> None:
    assert read_neighbour(ITEM, EntitySnapshot("sensor.hall_temperature", value)).temperature is None
    assert read_neighbour(ITEM, _vtherm(temperature=value)).temperature is None


def test_doors_are_open_when_any_sensor_is_on() -> None:
    closed = EntitySnapshot("binary_sensor.door_a", "off")
    open_ = EntitySnapshot("binary_sensor.door_b", "on")

    assert doors_open([closed, open_])
    assert not doors_open([closed])


@pytest.mark.parametrize("state", ["unknown", "unavailable", None])
def test_door_in_unknown_state_counts_as_closed(state) -> None:
    assert not doors_open([EntitySnapshot("binary_sensor.door", state), None])


@pytest.mark.parametrize(
    ("mode", "door_open", "expected"),
    [
        (MODE_NONE, True, False),
        (MODE_ALWAYS, False, True),
        (MODE_DOOR, False, False),
        (MODE_DOOR, True, True),
    ],
)
def test_connection_follows_the_mode(mode, door_open, expected) -> None:
    item = DependencyItem(item_id="n1", kind="neighbour", entity="reg", mode=mode, door_sensors=("reg-door",))

    assert connection_active(item, read_neighbour(item, _vtherm()), door_open) is expected
