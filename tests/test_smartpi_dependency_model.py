"""Tests for the dependency model: gates, neighbour window reactions, inputs and state."""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_COOL, VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import DOOR_SETTLING_S
from custom_components.vtherm_smartpi.smartpi.dependencies import DependencyItem, DependencyState
from custom_components.vtherm_smartpi.smartpi.dependency_inputs import (
    DependencyReadings,
    NeighbourObservation,
    NeighbourReading,
)
from custom_components.vtherm_smartpi.smartpi.dependency_model import (
    GATE_DOOR_OPEN,
    GATE_DOOR_SETTLING,
    GATE_NEIGHBOUR_WINDOW,
    DependencyModel,
    ModelContext,
)

PRESETS = {"frost_temp": 7.0, "eco_temp": 17.0, "eco_cool_temp": 27.0}


def _ctx(now_s: float = 0.0, hvac_mode=VThermHvacMode_HEAT) -> ModelContext:
    return ModelContext(
        now_s=now_s,
        zone_temp=20.0,
        ext_temp=5.0,
        power=0.4,
        a=0.03,
        b=0.001,
        deadtime_s=600.0,
        model_reliable=True,
        hvac_mode=hvac_mode,
    )


def _neighbour(
    item_id="hall",
    mode="door",
    door_open=False,
    window_open=False,
    action="nothing",
    thermostat=True,
    temperature=16.0,
) -> NeighbourObservation:
    item = DependencyItem(
        item_id=item_id,
        kind="neighbour",
        entity=f"reg-{item_id}",
        mode=mode,
        door_sensors=("reg-door",) if mode == "door" else (),
        neighbour_window_action=action,
    )
    reading = NeighbourReading(
        item_id=item_id,
        entity_id=f"climate.{item_id}",
        temperature=temperature,
        available=True,
        is_thermostat=thermostat,
        window_open=window_open and thermostat,
    )
    return NeighbourObservation(item, reading, door_open=door_open)


def _update(model, *neighbours, now_s=0.0, hvac_mode=VThermHvacMode_HEAT):
    return model.update(DependencyReadings(neighbours=neighbours, presets=PRESETS), _ctx(now_s, hvac_mode))


def test_without_readings_there_are_no_inputs() -> None:
    assert DependencyModel(DependencyState()).update(None, _ctx()) is None


def test_open_door_gates_learning_then_the_door_settles() -> None:
    model = DependencyModel(DependencyState())
    assert _update(model, _neighbour(door_open=False)).learning_gate is None
    assert _update(model, _neighbour(door_open=True), now_s=60.0).learning_gate == GATE_DOOR_OPEN

    closed_at = 600.0
    assert _update(model, _neighbour(door_open=False), now_s=closed_at).learning_gate == GATE_DOOR_SETTLING
    assert (
        _update(model, _neighbour(door_open=False), now_s=closed_at + DOOR_SETTLING_S - 1).learning_gate
        == GATE_DOOR_SETTLING
    )
    assert _update(model, _neighbour(door_open=False), now_s=closed_at + DOOR_SETTLING_S).learning_gate is None


def test_always_connection_does_not_gate_learning() -> None:
    model = DependencyModel(DependencyState())

    assert _update(model, _neighbour(mode="always")).learning_gate is None


def test_neighbour_window_gates_learning_only_while_connected() -> None:
    model = DependencyModel(DependencyState())

    assert _update(model, _neighbour(door_open=False, window_open=True)).learning_gate is None
    assert (
        _update(model, _neighbour(door_open=True, window_open=True), now_s=60.0).learning_gate
        == GATE_NEIGHBOUR_WINDOW
    )


def test_sensor_neighbour_has_no_window() -> None:
    model = DependencyModel(DependencyState())
    inputs = _update(model, _neighbour(mode="always", window_open=True, thermostat=False, action="turn_off"))

    assert inputs.learning_gate is None
    assert inputs.control_action is None


@pytest.mark.parametrize(
    ("action", "hvac_mode", "expected"),
    [
        ("nothing", VThermHvacMode_HEAT, (None, None)),
        ("turn_off", VThermHvacMode_HEAT, ("turn_off", None)),
        ("frost", VThermHvacMode_HEAT, ("frost", 7.0)),
        ("eco", VThermHvacMode_HEAT, ("eco", 17.0)),
        ("eco", VThermHvacMode_COOL, ("eco", 27.0)),
        ("frost", VThermHvacMode_COOL, ("turn_off", None)),
    ],
)
def test_window_reaction_follows_the_chosen_action(action, hvac_mode, expected) -> None:
    model = DependencyModel(DependencyState())
    inputs = _update(model, _neighbour(mode="always", window_open=True, action=action), hvac_mode=hvac_mode)

    assert (inputs.control_action, inputs.action_setpoint) == expected


def test_most_protective_reaction_wins() -> None:
    model = DependencyModel(DependencyState())
    inputs = _update(
        model,
        _neighbour("hall", mode="always", window_open=True, action="eco"),
        _neighbour("kitchen", mode="always", window_open=True, action="turn_off"),
    )

    assert inputs.control_action == "turn_off"


def test_missing_preset_falls_back_to_turning_off() -> None:
    model = DependencyModel(DependencyState())
    inputs = model.update(
        DependencyReadings(neighbours=(_neighbour(mode="always", window_open=True, action="eco"),)),
        _ctx(),
    )

    assert (inputs.control_action, inputs.action_setpoint) == ("turn_off", None)


def test_exchange_inputs_and_status_use_learned_k_only() -> None:
    state = DependencyState()
    model = DependencyModel(state)
    unlearned = _update(model, _neighbour(mode="always"))
    assert unlearned.exchanges == ()
    assert unlearned.status[0]["k"] is None
    assert unlearned.status[0]["active"] is True

    state.for_item("hall")["k_history"] = [0.002] * 8
    model.load()
    learned = _update(model, _neighbour(door_open=True))
    assert [(x.key, x.conductance, x.temperature) for x in learned.exchanges] == [("hall", 0.002, 16.0)]
    status = learned.status[0]
    assert status["k"] == 0.002
    assert status["k_reliable"] is True
    assert status["share_of_losses"] == pytest.approx(0.002 * 4.0 / (0.001 * 15.0 + 0.002 * 4.0), abs=1e-3)

    closed = _update(model, _neighbour(door_open=False), now_s=60.0)
    assert closed.exchanges == ()


def test_state_survives_a_restart() -> None:
    state = DependencyState()
    state.for_item("hall")["k_history"] = [0.0019, 0.002, 0.0021] * 3

    restarted = DependencyModel(state)
    inputs = _update(restarted, _neighbour(mode="always"))

    assert inputs.exchanges[0].conductance == pytest.approx(0.002)
