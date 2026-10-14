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


def _ctx(now_s: float = 0.0, hvac_mode=VThermHvacMode_HEAT, learning_allowed=True) -> ModelContext:
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
        learning_allowed=learning_allowed,
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


@pytest.mark.parametrize(
    ("eco_temp", "expected"),
    [
        (0.0, ("turn_off", None)),
        (35.0, ("turn_off", None)),
        (5.0, ("turn_off", None)),
        (17.0, ("eco", 17.0)),
    ],
    ids=["not_configured", "above_the_range", "below_the_range", "usable"],
)
def test_preset_outside_the_thermostat_range_falls_back_to_turning_off(eco_temp, expected) -> None:
    """Versatile Thermostat reports 0 for a preset that is not configured."""
    model = DependencyModel(DependencyState())
    inputs = model.update(
        DependencyReadings(
            neighbours=(_neighbour(mode="always", window_open=True, action="eco"),),
            presets={"eco_temp": eco_temp},
            temp_range=(7.0, 30.0),
        ),
        _ctx(),
    )

    assert (inputs.control_action, inputs.action_setpoint) == expected


def _steady(model, minutes, door_open=True, start_min=0, learning_allowed=True):
    """A steady zone at 20 degC held by a warmer door neighbour, one calculation a minute."""
    for minute in range(start_min, start_min + minutes):
        model.update(
            DependencyReadings(neighbours=(_neighbour(door_open=door_open, temperature=24.0),), presets=PRESETS),
            _ctx(minute * 60.0, learning_allowed=learning_allowed),
        )


def test_k_learns_only_when_smartpi_could_learn() -> None:
    allowed, paused = DependencyModel(DependencyState()), DependencyModel(DependencyState())

    _steady(allowed, 120)
    _steady(paused, 120, learning_allowed=False)

    assert allowed._coupling.samples("hall") > 0
    assert paused._coupling.samples("hall") == 0


def test_k_waits_for_the_door_to_settle() -> None:
    """The first window after a door opens starts once the air has settled."""
    model = DependencyModel(DependencyState())
    _steady(model, 10, door_open=False)
    _steady(model, 35, door_open=True, start_min=10)

    assert model._coupling.samples("hall") == 0

    _steady(model, 10, door_open=True, start_min=45)
    assert model._coupling.samples("hall") == 1


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


def test_always_connection_state_survives_a_restart() -> None:
    """The joint buffer and fits of always-connected neighbours are saved without any door rows."""
    state = DependencyState()
    model = DependencyModel(state)
    joint = {
        "joint_rows": [[-0.016, {"hall": 1.0, "_ext": -15.0}]] * 3,
        "joint_k": {"hall": [0.0015] * 8},
        "joint_b": [0.001] * 8,
    }
    model._coupling.load_shared(joint)
    state.for_item("hall")
    model.forget_except({"hall"})

    restarted = DependencyModel(state)
    inputs = _update(restarted, _neighbour(mode="always"))

    assert inputs.exchanges[0].conductance == pytest.approx(0.0015)
    assert inputs.loss_coefficient == pytest.approx(0.001)
    assert inputs.status[0]["k_samples"] == 3


def test_the_fitted_loss_enters_with_its_always_connected_exchange() -> None:
    """SmartPI's b holds the exchange until it is known; the fitted loss replaces it at the
    pace the exchange fades in, so b_eff does not step down and climb back."""
    from custom_components.vtherm_smartpi.smartpi.const import KNOWN_INPUT_FADE_S
    from custom_components.vtherm_smartpi.smartpi.effective_model import compose

    model = DependencyModel(DependencyState())
    model._coupling.load_shared(
        {"joint_rows": [[-0.016, {"hall": 1.0, "_ext": -15.0}]] * 3, "joint_k": {"hall": [0.0015] * 8}, "joint_b": [0.001] * 8}
    )
    learned_b = 0.0025  # the loss 0.001 and the exchange 0.0015 together

    weights = []
    for now_s in (0.0, KNOWN_INPUT_FADE_S / 4, KNOWN_INPUT_FADE_S / 2, 2 * KNOWN_INPUT_FADE_S):
        inputs = _update(model, _neighbour(mode="always"), now_s=now_s)
        weights.append(inputs.loss_weight)
        assert compose(learned_b, 5.0, inputs).b == pytest.approx(learned_b)

    assert weights == pytest.approx([0.0, 0.25, 0.5, 1.0])
    assert compose(learned_b, 5.0, inputs).ext_temp == pytest.approx((0.001 * 5.0 + 0.0015 * 16.0) / learned_b)


def test_always_connected_neighbours_enter_together_once_each_is_known() -> None:
    """SmartPI's b holds both exchanges: one known alone stays out, so neither is dropped nor
    counted twice; once both are known they fade in together and b_eff does not step."""
    from custom_components.vtherm_smartpi.smartpi.const import KNOWN_INPUT_FADE_S
    from custom_components.vtherm_smartpi.smartpi.effective_model import compose

    model = DependencyModel(DependencyState())
    hall = _neighbour("hall", mode="always", temperature=16.0)
    bed = _neighbour("bed", mode="always", temperature=18.0)
    learned_b = 0.0035  # the loss 0.001 and both exchanges, 0.0015 and 0.001
    model._coupling.load_shared({"joint_k": {"hall": [0.0015] * 8}, "joint_b": [0.001] * 8})

    one_known = _update(model, hall, bed, now_s=0.0)
    assert one_known.exchanges == () and one_known.loss_coefficient is None

    model._coupling.load_shared({"joint_k": {"hall": [0.0015] * 8, "bed": [0.001] * 8}, "joint_b": [0.001] * 8})
    for now_s in (600.0, 600.0 + KNOWN_INPUT_FADE_S / 2, 600.0 + 2 * KNOWN_INPUT_FADE_S):
        inputs = _update(model, hall, bed, now_s=now_s)
        assert compose(learned_b, 5.0, inputs).b == pytest.approx(learned_b)
    assert inputs.loss_weight == 1.0
    assert sorted(e.conductance for e in inputs.exchanges) == pytest.approx([0.001, 0.0015])


def test_unreadable_saved_state_is_dropped() -> None:
    state = DependencyState()
    state.for_item("hall")["k_history"] = [0.002] * 8
    state.shared["coupling"] = {"joint_k": ["not", "a", "mapping"], "joint_b": "nope"}
    state.items["hall"] = ["not", "a", "mapping"]

    model = DependencyModel(state)

    assert _update(model, _neighbour(mode="always")).exchanges == ()
