"""Edge cases of the dependency model.

They check what the model keeps when the same source is configured again or a
source coefficient learned in cooling is restored, what it skips without an
outdoor temperature, a lead demand or a target temperature, that a fitted
exchange which is not reliable stays out, that a coefficient published during
a recovery starts fading in only once the recovery is over, that saved fits
whose emitter response cannot be read are dropped, and that a neighbour's eco
reaction in cooling holds the source to the warmer setpoint.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.vtherm_smartpi.hvac_mode import (
    VThermHvacMode_COOL,
    VThermHvacMode_HEAT,
)
from custom_components.vtherm_smartpi.smartpi.const import (
    KNOWN_INPUT_FADE_S,
    SOURCE_LEARN_BLOCK_MIN_S,
    SOURCE_LEARN_LOW,
)
from custom_components.vtherm_smartpi.smartpi.dependencies import (
    KIND_SOURCE,
    DependencyItem,
    DependencyState,
)
from custom_components.vtherm_smartpi.smartpi.dependency_inputs import (
    DependencyReadings,
    NeighbourObservation,
    NeighbourReading,
    SourceObservation,
)
from custom_components.vtherm_smartpi.smartpi.dependency_model import (
    GATE_DOOR_OPEN,
    DependencyModel,
    ModelContext,
    cool_key,
)
from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings
from custom_components.vtherm_smartpi.smartpi.zone_fit import ZoneFitResult

SPLIT = {
    "split_rule": "lead_band",
    "lead_band_percent": 20,
    "lead_emitter": "radiator",
    "source_emitter": "electric_fast",
}
SOURCE = SourceObservation("s1", "climate.floor", True, (), 1.0)


def _settings(**extra) -> SourceSettings:
    return SourceSettings.from_item(
        DependencyItem(item_id="s1", kind=KIND_SOURCE, entity="reg-src", extra={**SPLIT, **extra})
    )


def _model(state: DependencyState | None = None) -> DependencyModel:
    model = DependencyModel(DependencyState() if state is None else state)
    model.configure_source(_settings())
    return model


def _ctx(
    now_s=0.0,
    zone=20.0,
    target=20.0,
    ext=5.0,
    demand=0.3,
    output=0.3,
    learning_phase=False,
    hvac=VThermHvacMode_HEAT,
) -> ModelContext:
    return ModelContext(
        now_s=now_s,
        zone_temp=zone,
        ext_temp=ext,
        power=output,
        a=0.03,
        b=0.001,
        deadtime_s=600.0,
        model_reliable=not learning_phase,
        hvac_mode=hvac,
        learning_phase=learning_phase,
        lead_demand=demand,
        lead_output=output,
        target_temp=target,
    )


def _readings(*neighbours, source=SOURCE, presets=None) -> DependencyReadings:
    return DependencyReadings(neighbours=neighbours, source=source, presets=presets or {})


def _neighbour(mode="always", door_open=False, window_open=False, action="nothing", temperature=16.0):
    item = DependencyItem(
        item_id="hall",
        kind="neighbour",
        entity="reg-hall",
        mode=mode,
        door_sensors=("reg-door",) if mode == "door" else (),
        neighbour_window_action=action,
    )
    reading = NeighbourReading(
        item_id="hall",
        entity_id="climate.hall",
        temperature=temperature,
        available=True,
        is_thermostat=True,
        window_open=window_open,
    )
    return NeighbourObservation(item, reading, door_open=door_open)


def _fit(k=(), k_se=()) -> ZoneFitResult:
    return ZoneFitResult(
        a=0.03, b=0.001, a_f=0.03, a_f_se=0.001, candidate=4, windows=50.0, k=k, lead_delay_s=600.0, k_se=k_se
    )


def _state_with(allocation=None, x_f=None, a_f=None) -> DependencyState:
    state = DependencyState()
    item = state.for_item("s1")
    if allocation is not None:
        item["allocation"] = allocation
    if x_f is not None:
        item["x_f"] = x_f
    if a_f is not None:
        engine = _model()._source.fits["heat"]
        engine.result = replace(_fit(), a_f=a_f)
        item["zone_fit"] = engine.save_state()
    return state


def _run(model, start_s, end_s, step_s=600.0, readings=None, **ctx):
    """Calculations every ``step_s`` from ``start_s`` to ``end_s``; return the last inputs."""
    inputs, now_s = None, start_s
    while now_s <= end_s:
        inputs = model.update(readings or _readings(), _ctx(now_s=now_s, **ctx))
        now_s += step_s
    return inputs


def test_configuring_the_same_source_again_keeps_what_it_learned() -> None:
    model = _model()
    model.update(_readings(), _ctx())
    model._source.fits["heat"].result = _fit()
    source = model._source

    model.configure_source(_settings())
    assert model._source is source
    assert model.zone_fit(VThermHvacMode_HEAT) == _fit()

    model.configure_source(_settings(split_rule="together"))
    assert model._source is not source
    assert model.zone_fit(VThermHvacMode_HEAT) is None


def test_a_source_coefficient_learned_in_cooling_survives_a_restart() -> None:
    state = DependencyState()
    state.for_item("s1")["cool"] = {"k_history": [0.02] * 8}

    restarted = _model(state)
    assert restarted._coupling.k(cool_key("s1")) == pytest.approx(0.02)
    # Known before the restart: fully in the control law at once.
    assert restarted._fade_weight(cool_key("s1"), 0.0) == 1.0

    restarted.update(_readings(), _ctx(hvac=VThermHvacMode_COOL))
    assert state.items["s1"]["cool"] == {"k_history": [0.02] * 8}


def test_without_an_outdoor_temperature_no_exchange_is_learned() -> None:
    """The door still gates SmartPI's learning; the exchange windows wait for the outdoor reading."""
    blind, seeing = DependencyModel(DependencyState()), DependencyModel(DependencyState())
    door = _neighbour(mode="door", door_open=True, temperature=24.0)

    for minute in range(120):
        inputs = blind.update(_readings(door, source=None), _ctx(now_s=minute * 60.0, ext=None))
        seeing.update(_readings(door, source=None), _ctx(now_s=minute * 60.0))

    assert inputs.learning_gate == GATE_DOOR_OPEN
    assert blind._coupling.samples("hall") == 0
    assert seeing._coupling.samples("hall") > 0


def test_without_an_outdoor_temperature_the_zone_fit_gets_no_sample() -> None:
    model = _model()
    _run(model, 0.0, 3 * 3600.0, step_s=60.0, ext=None)

    assert model._source.fits["heat"].windows == 0.0
    assert model._source.fits["heat"]._samples == []


def test_an_always_connected_exchange_the_fit_cannot_give_reliably_stays_out() -> None:
    """The fit's k for the neighbour has a standard error as large as itself, and no k of its own."""
    model = _model()
    hall = _neighbour()
    model.update(_readings(hall), _ctx(now_s=0.0))
    model._source.fits["heat"].result = _fit(k=(("hall", 0.002),), k_se=(("hall", 0.002),))

    inputs = model.update(_readings(hall), _ctx(now_s=60.0))

    assert inputs.exchanges == ()
    assert inputs.status[0]["k"] is None
    assert "hall" not in model._known_since


def test_a_coefficient_published_during_a_recovery_fades_in_once_it_is_over() -> None:
    """While the zone recovers the lead works alone: the source's heat is not counted, not even
    as the lead's handover in the brake; it starts fading in when the recovery ends."""
    model = _model(_state_with(allocation=0.6, x_f=0.6))
    model.update(_readings(), _ctx(demand=0.3))
    model._source.fits["heat"].result = _fit()

    boosted = model.update(_readings(), _ctx(now_s=60.0, zone=18.0, demand=0.3, output=1.0))
    assert boosted.source["recovery_boost"] is True
    assert boosted.heats == ()
    assert model._fade_weight("s1", 60.0) == 0.0

    recovered = model.update(_readings(), _ctx(now_s=120.0, demand=0.3))
    assert recovered.source["recovery_boost"] is False
    assert recovered.heats == ()

    half = model.update(_readings(), _ctx(now_s=120.0 + KNOWN_INPUT_FADE_S / 2, demand=0.3))
    assert half.heats[0].rate == pytest.approx(0.5 * 0.03 * model._source.x_f)


@pytest.mark.parametrize(
    "saved",
    ["floor_water", [None, None], [], {"lag_s": 60.0}],
    ids=["text", "not_numbers", "empty", "mapping"],
)
def test_saved_fits_whose_emitter_response_cannot_be_read_are_dropped(saved) -> None:
    state = _state_with(a_f=0.03)
    state.items["s1"]["response_start"] = saved

    restarted = _model(state)
    restarted.update(_readings(), _ctx())

    assert restarted.zone_fit(VThermHvacMode_HEAT) is None


def test_a_neighbour_s_eco_reaction_in_cooling_holds_the_source_to_the_warmer_setpoint() -> None:
    """At 25 degC for 24 the source would cool at full; held to the eco 27 degC it stops."""
    window = _neighbour(window_open=True, action="eco")
    eco = _model(_state_with(allocation=0.6))
    held = eco.update(
        _readings(window, presets={"eco_cool_temp": 27.0}), _ctx(zone=25.0, target=24.0, hvac=VThermHvacMode_COOL)
    )
    plain = _model(_state_with(allocation=0.6))
    calling = plain.update(_readings(), _ctx(zone=25.0, target=24.0, hvac=VThermHvacMode_COOL))

    assert (held.control_action, held.action_setpoint) == ("eco", 27.0)
    assert held.source_command == 0.0
    assert calling.source_command == 1.0


def test_without_a_lead_demand_the_allocation_loop_holds() -> None:
    """The loop pauses: it keeps its command and does not integrate over the time without demand."""
    model = _model(_state_with(allocation=0.6))
    waiting = _run(model, 0.0, 3 * 3600.0, demand=None)

    assert waiting.source_command == pytest.approx(0.6)
    assert waiting.source["lead_demand_avg"] is None
    assert model._source.loop.command == 0.6

    model.update(_readings(), _ctx(now_s=3 * 3600.0 + 600.0, demand=1.5))
    assert model._source.loop.command == 0.6


def test_without_a_target_the_source_follows_its_loop_alone() -> None:
    """No error to answer: no proportional correction and no full-power timer for a lead at its limit."""
    model = _model(_state_with(allocation=0.6))
    inputs = _run(model, 0.0, 600.0, zone=15.0, target=None, demand=1.5, output=1.0)
    with_target = _model(_state_with(allocation=0.6))
    calling = _run(with_target, 0.0, 600.0, zone=15.0, demand=1.5, output=1.0)

    assert model._source.full_since_s is None
    assert inputs.source_command == pytest.approx(model._source.loop.command)
    assert inputs.source_command < 1.0
    assert with_target._source.full_since_s == 0.0
    assert calling.source_command == 1.0


def test_without_a_target_the_learning_blocks_run_without_the_cold_override() -> None:
    state = DependencyState()
    state.for_item("s1")["learn_elapsed_s"] = {"heat": SOURCE_LEARN_BLOCK_MIN_S + 60.0}
    with_target = DependencyState()
    with_target.for_item("s1")["learn_elapsed_s"] = {"heat": SOURCE_LEARN_BLOCK_MIN_S + 60.0}

    blind = _model(state).update(_readings(), _ctx(zone=15.0, target=None, learning_phase=True))
    cold = _model(with_target).update(_readings(), _ctx(zone=15.0, learning_phase=True))

    assert blind.source_command == SOURCE_LEARN_LOW
    assert cold.source_command == 1.0
