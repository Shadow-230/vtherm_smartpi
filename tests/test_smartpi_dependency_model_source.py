"""Tests for the additional source in the dependency model: learning blocks, allocation,
predictive brake, the zone fit's gate, known heat and state."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from custom_components.vtherm_smartpi.hvac_mode import (
    VThermHvacMode_COOL,
    VThermHvacMode_HEAT,
    VThermHvacMode_OFF,
)
from custom_components.vtherm_smartpi.smartpi.const import (
    KNOWN_INPUT_FADE_S,
    SOURCE_LEARN_BLOCK_MIN_S,
    SOURCE_LEARN_HIGH,
    SOURCE_LEARN_LOW,
    SPLIT_DEMAND_MAX,
    ZONE_FIT_TIMEOUT_S,
)
from custom_components.vtherm_smartpi.smartpi.dependencies import KIND_SOURCE, DependencyItem, DependencyState
from custom_components.vtherm_smartpi.smartpi.dependency_inputs import DependencyReadings, SourceObservation
from custom_components.vtherm_smartpi.smartpi.dependency_model import (
    GATE_SOURCE_HEAT,
    SOURCE_MODE_ALLOCATION,
    SOURCE_MODE_LEARN,
    SOURCE_MODE_OFF,
    DependencyModel,
    ModelContext,
    cool_key,
)
from custom_components.vtherm_smartpi.smartpi.source_link import SourceCycle
from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings
from custom_components.vtherm_smartpi.smartpi.zone_fit import ZoneFitResult

SPLIT = {
    "split_rule": "lead_band",
    "lead_band_percent": 20,
    "lead_emitter": "radiator",
    "source_emitter": "electric_fast",
}


def _model(state: DependencyState | None = None) -> DependencyModel:
    model = DependencyModel(DependencyState() if state is None else state)
    model.configure_source(
        SourceSettings.from_item(DependencyItem(item_id="s1", kind=KIND_SOURCE, entity="reg-src", extra=SPLIT))
    )
    return model


def _ctx(now_s=0.0, zone=20.0, target=20.0, demand=0.3, output=0.3, learning_phase=False, hvac=VThermHvacMode_HEAT):
    return ModelContext(
        now_s=now_s,
        zone_temp=zone,
        ext_temp=5.0,
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


def _readings(active=True, cycles=(), power_cap=1.0):
    return DependencyReadings(source=SourceObservation("s1", "climate.floor", active, tuple(cycles), power_cap))


def _fit(a=0.03, b=0.001, a_f=0.03, k=()) -> ZoneFitResult:
    return ZoneFitResult(a=a, b=b, a_f=a_f, a_f_se=0.001, candidate=4, windows=50.0, k=k, lead_delay_s=600.0)


def _published(model: DependencyModel, fit: ZoneFitResult | None = None, mode: str = "heat") -> DependencyModel:
    model._source.fits[mode].result = fit or _fit()
    return model


def _cycle(start_s, power=1.0):
    return SourceCycle(start_s=start_s, on_s=power * 600.0, duration_s=600.0, power=power, switched=True)


def test_without_an_active_source_it_gets_nothing() -> None:
    model = _model()
    inputs = model.update(_readings(active=False), _ctx())

    assert inputs.source_command == 0.0
    assert inputs.source["mode"] == SOURCE_MODE_OFF
    assert inputs.heats == ()
    assert inputs.learning_gate is None


def _run(model, start_s, end_s, step_s=600.0, readings=None, **ctx):
    """Calculations every ``step_s`` from ``start_s`` to ``end_s``; return the last inputs."""
    inputs, now_s = None, start_s
    while now_s <= end_s:
        inputs = model.update(readings or _readings(), _ctx(now_s=now_s, **ctx))
        now_s += step_s
    return inputs


def test_while_smartpi_learns_the_source_works_in_blocks() -> None:
    """Both sources work from the start: the source alternates between two commands."""
    model = _model()
    first = model.update(_readings(), _ctx(learning_phase=True))
    assert (first.source["mode"], first.source_command) == (SOURCE_MODE_LEARN, SOURCE_LEARN_HIGH)
    second = _run(model, 600.0, SOURCE_LEARN_BLOCK_MIN_S + 600.0, learning_phase=True)
    assert second.source_command == SOURCE_LEARN_LOW
    third = _run(model, SOURCE_LEARN_BLOCK_MIN_S + 1200.0, 2 * SOURCE_LEARN_BLOCK_MIN_S + 600.0, learning_phase=True)
    assert third.source_command == SOURCE_LEARN_HIGH


def test_the_learning_clock_counts_the_source_at_work_per_hvac_mode() -> None:
    """Only time with the source active and the mode's fit awaited counts; gaps do not."""
    model = _model()
    _run(model, 0.0, 3600.0, learning_phase=True)
    assert model._source.learn_elapsed_s == {"heat": pytest.approx(3600.0), "cool": 0.0}

    _run(model, 4200.0, 7800.0, readings=_readings(active=False), learning_phase=True)
    _run(model, 4 * 3600.0 + 10_000.0, 4 * 3600.0 + 10_600.0, learning_phase=True)
    assert model._source.learn_elapsed_s["heat"] == pytest.approx(3600.0 + 600.0)

    _run(model, 30_000.0, 31_200.0, learning_phase=True, hvac=VThermHvacMode_COOL)
    assert model._source.learn_elapsed_s["cool"] == pytest.approx(1200.0)
    _published(model)
    _run(model, 31_800.0, 33_000.0)
    assert model._source.learn_elapsed_s["heat"] == pytest.approx(4200.0)


def test_while_smartpi_learns_a_cold_zone_gets_the_source_and_a_warm_one_none() -> None:
    model = _model()
    cold = model.update(_readings(), _ctx(now_s=SOURCE_LEARN_BLOCK_MIN_S + 1.0, zone=19.4, learning_phase=True))
    assert cold.source_command == 1.0
    warm = model.update(_readings(), _ctx(now_s=60.0, zone=20.4, learning_phase=True))
    assert warm.source_command == 0.0


def test_after_learning_the_allocation_loop_drives_the_source() -> None:
    model = _model()
    model.update(_readings(), _ctx(demand=0.6))
    inputs = model.update(_readings(), _ctx(now_s=3600.0, demand=0.6))

    assert inputs.source["mode"] == SOURCE_MODE_ALLOCATION
    assert inputs.source_command > 0.0


def test_a_lead_stuck_at_a_limit_moves_the_source_at_full_rate() -> None:
    """At 0 % with a warm zone the source comes down; at 100 % with a cold zone it rises after settling.

    The allocation alone is checked here; the command also carries the
    proportional answer to the zone's error.
    """
    warm = _model(_state_with(allocation=0.5))
    warm.update(_readings(), _ctx(zone=20.5, demand=0.5, output=0.0))
    warm.update(_readings(), _ctx(now_s=600.0, zone=20.5, demand=0.5, output=0.0))
    assert warm._source.loop.command < 0.5

    cold = _model(_state_with(allocation=0.5))
    settle = cold._source.settings.source.settle_s
    cold.update(_readings(), _ctx(zone=19.5, demand=0.2, output=1.0))
    cold.update(_readings(), _ctx(now_s=settle - 60.0, zone=19.5, demand=0.2, output=1.0))
    assert cold._source.loop.command <= 0.5
    cold.update(_readings(), _ctx(now_s=settle, zone=19.5, demand=0.2, output=1.0))
    assert cold._allocation_demand(_ctx(now_s=settle + 60.0, zone=19.5, demand=0.2, output=1.0)) == SPLIT_DEMAND_MAX


def test_unknown_source_heat_gates_learning_until_the_zone_fit_is_published() -> None:
    model = _model()
    for minute in range(0, 30):
        inputs = model.update(_readings(cycles=[_cycle(minute * 60.0 - 600.0)]), _ctx(now_s=minute * 60.0))
    assert inputs.learning_gate == GATE_SOURCE_HEAT

    _published(model)
    inputs = model.update(_readings(cycles=[_cycle(1800.0)]), _ctx(now_s=1860.0))
    assert inputs.learning_gate is None


def test_a_fit_late_beyond_its_timeout_lets_smartpi_learn_on_its_own() -> None:
    model = _model()
    model.update(_readings(cycles=[_cycle(-600.0)]), _ctx(learning_phase=True))
    gated = model.update(_readings(cycles=[_cycle(0.0)]), _ctx(now_s=300.0, learning_phase=True))
    assert gated.learning_gate == GATE_SOURCE_HEAT
    model._source.learn_elapsed_s["heat"] = ZONE_FIT_TIMEOUT_S - 300.0
    late = model.update(_readings(cycles=[_cycle(600.0)]), _ctx(now_s=900.0, learning_phase=True))
    assert late.learning_gate is None
    assert late.source["zone_fit"]["timed_out"] is True
    # Cooling has its own clock.
    cooling = model.update(_readings(cycles=[_cycle(1200.0)]), _ctx(now_s=1500.0, hvac=VThermHvacMode_COOL))
    assert cooling.source["zone_fit"]["timed_out"] is False


def test_a_lead_already_learned_waits_for_the_fit_at_most_the_timeout() -> None:
    """A source added next to a SmartPI that learned on its own: allocation at once,
    and the gate opens once the fit has not come in time."""
    model = _model()
    first = model.update(_readings(cycles=[_cycle(-600.0)]), _ctx(demand=0.5))
    assert first.source["mode"] == SOURCE_MODE_ALLOCATION
    late = _run(
        model, 600.0, ZONE_FIT_TIMEOUT_S + 1200.0, step_s=900.0, readings=_readings(cycles=[_cycle(0.0)]), demand=0.5
    )
    assert late.learning_gate is None
    assert late.source["zone_fit"]["timed_out"] is True


def _state_with(a_f=None, allocation=None, x_f=None, cool_a_f=None) -> DependencyState:
    """A saved state; ``a_f`` (and ``cool_a_f``) come as a published zone fit."""
    state = DependencyState()
    item = state.for_item("s1")
    for key, value in (("zone_fit", a_f), ("zone_fit_cool", cool_a_f)):
        if value is not None:
            engine = _model()._source.fits["heat"]
            engine.result = _fit(a_f=value)
            item[key] = engine.save_state()
    if allocation is not None:
        item["allocation"] = allocation
    if x_f is not None:
        item["x_f"] = x_f
    return state


def test_a_known_source_is_a_known_heat_input() -> None:
    """Loaded as known, its heat a_f*x_f is fully in the model, with a trajectory."""
    model = _model(_state_with(a_f=0.03, x_f=0.5))
    inputs = model.update(_readings(), _ctx(now_s=0.0))

    (heat,) = inputs.heats
    assert heat.rate == pytest.approx(0.03 * 0.5)
    assert heat.at(120.0) < heat.rate


def test_a_newly_known_coefficient_enters_gradually() -> None:
    model = _model(_state_with(x_f=1.0))
    model.update(_readings(), _ctx(now_s=0.0))
    _published(model)

    first = model.update(_readings(cycles=[_cycle(-600.0)]), _ctx(now_s=100.0))
    half = model.update(_readings(), _ctx(now_s=100.0 + KNOWN_INPUT_FADE_S / 2))
    full = model.update(_readings(), _ctx(now_s=100.0 + 2 * KNOWN_INPUT_FADE_S))

    assert first.heats == ()
    assert half.heats[0].rate == pytest.approx(0.5 * 0.03 * model._source.x_f, rel=0.05)
    assert full.heats[0].rate == pytest.approx(0.03 * model._source.x_f)


def test_in_cooling_the_source_has_its_own_coefficient_and_a_negative_effect() -> None:
    state = _state_with(x_f=0.5, cool_a_f=0.02)
    model = _model(state)

    assert model._source_regressor(_ctx(hvac=VThermHvacMode_COOL), 0.5) == {cool_key("s1"): -0.5}
    inputs = model.update(_readings(), _ctx(hvac=VThermHvacMode_COOL))
    assert inputs.heats[0].rate == pytest.approx(-0.02 * model._source.x_f)
    heating = model.update(_readings(), _ctx(now_s=60.0, hvac=VThermHvacMode_HEAT))
    assert heating.heats == ()
    assert model.zone_fit(VThermHvacMode_COOL).a_f == pytest.approx(0.02)
    assert model.zone_fit(VThermHvacMode_HEAT) is None


def test_a_lead_switched_off_gives_its_source_nothing() -> None:
    model = _model(_state_with(allocation=0.7))
    inputs = model.update(_readings(), _ctx(hvac=VThermHvacMode_OFF))

    assert (inputs.source["mode"], inputs.source_command) == (SOURCE_MODE_OFF, 0.0)


def test_the_allocation_and_x_f_survive_a_restart() -> None:
    state = DependencyState()
    model = _model(state)
    model.update(_readings(), _ctx(demand=0.9))
    model.update(_readings(cycles=[_cycle(0.0)]), _ctx(now_s=7200.0, demand=0.9))
    command = model.source_command

    restarted = _model(state)
    inputs = restarted.update(_readings(), _ctx(now_s=10.0, demand=0.9))

    assert command > 0.0
    assert inputs.source["command"] == pytest.approx(command, abs=0.01)
    assert restarted._source.x_f > 0.0


def test_a_source_that_stops_following_gets_nothing_and_its_heat_is_unknown() -> None:
    """When the source no longer applies the lead's commands, its command drops to 0 at once."""
    model = _model(_state_with(allocation=0.7))
    running = model.update(_readings(), _ctx(demand=0.6))
    stopped = model.update(_readings(active=False), _ctx(now_s=60.0, demand=0.6))

    assert running.source_command == pytest.approx(0.7)
    assert (stopped.source_command, stopped.source["mode"], stopped.heats) == (0.0, SOURCE_MODE_OFF, ())


def test_the_source_answers_a_lasting_error_in_proportion() -> None:
    """Besides the allocation, the source helps correct the zone's error, and lets go once it is gone."""
    model = _model(_state_with(allocation=0.4))
    cold = model.update(_readings(), _ctx(zone=19.8, demand=0.2))
    settled = model.update(_readings(), _ctx(now_s=60.0, zone=20.0, demand=0.2))

    assert cold.source_command == pytest.approx(0.4 + 2.0 * 0.2, abs=0.01)
    assert settled.source_command == pytest.approx(0.4, abs=0.01)


def _window_neighbour(action: str):
    from custom_components.vtherm_smartpi.smartpi.dependency_inputs import NeighbourObservation, NeighbourReading

    return NeighbourObservation(
        DependencyItem(item_id="hall", kind="neighbour", entity="reg-hall", mode="always", neighbour_window_action=action),
        NeighbourReading(
            item_id="hall", entity_id="climate.hall", temperature=16.0, available=True, is_thermostat=True, window_open=True
        ),
    )


def test_a_neighbour_window_reaction_applies_to_the_source() -> None:
    """Switched off with the lead, or held to the same frost setpoint instead of the comfort one."""
    source = SourceObservation("s1", "climate.floor", True)
    off = _model(_state_with(allocation=0.6))
    inputs = off.update(DependencyReadings(neighbours=(_window_neighbour("turn_off"),), source=source), _ctx(zone=19.5))
    assert (inputs.source_command, inputs.source["mode"]) == (0.0, SOURCE_MODE_OFF)

    frost = _model(_state_with(allocation=0.6))
    readings = DependencyReadings(
        neighbours=(_window_neighbour("frost"),), source=source, presets={"frost_temp": 7.0}
    )
    first = frost.update(readings, _ctx(zone=19.5))
    later = frost.update(readings, _ctx(now_s=3600.0, zone=19.5))
    assert first.source_command == 0.0
    assert later.source_command == 0.0


def test_the_source_heat_gate_holds_between_its_thresholds() -> None:
    """On from SOURCE_X_MIN, off below SOURCE_X_OFF: a source running low does not flap the gate."""
    model = _model()
    assert not model._source_gate(0.15, None, 0.0)
    assert model._source_gate(0.25, None, 0.0)
    assert model._source_gate(0.1, None, 0.0)
    assert not model._source_gate(0.04, None, 0.0)
    assert not model._source_gate(0.3, 0.03, 0.0)


def test_a_calibration_of_the_lead_holds_the_source() -> None:
    """A calibration drives the zone on purpose: the source neither reacts to it nor lets go."""

    def run(calibrating: bool) -> tuple[float, float]:
        model = _model(_state_with(allocation=0.4))
        before = model.update(_readings(), _ctx(demand=0.4)).source_command
        cold = _ctx(now_s=60.0, zone=18.0, demand=1.0)
        after = model.update(_readings(), replace(cold, calibrating=calibrating)).source_command
        return before, after

    before, held = run(calibrating=True)
    _, answered = run(calibrating=False)

    assert held == before
    assert answered > before

    # Two hours of calibration do not move the loop once it runs again.
    model = _model(_state_with(allocation=0.4))
    model.update(_readings(), _ctx(demand=0.4))
    loop = model._source.loop.command
    for minute in range(1, 120):
        model.update(_readings(), replace(_ctx(now_s=minute * 60.0, zone=18.0, demand=1.0), calibrating=True))
    model.update(_readings(), _ctx(now_s=7260.0, demand=0.4))
    assert model._source.loop.command == pytest.approx(loop, abs=1e-6)


def test_the_brake_eases_the_source_off_before_the_setpoint() -> None:
    """With heat on the way, the fitted model caps the source below its proportional answer."""
    free = _model(_state_with(allocation=0.4))
    braked = _published(_model(_state_with(allocation=0.4, x_f=1.0)))
    zone = 19.8  # 0.2 degC below the setpoint
    free_inputs = free.update(_readings(), _ctx(zone=zone, demand=0.2))
    braked_inputs = braked.update(_readings(), _ctx(zone=zone, demand=0.2))
    assert free_inputs.source_command == pytest.approx(0.4 + 2.0 * 0.2, abs=0.01)
    assert braked_inputs.source_command < free_inputs.source_command


def test_the_brake_lets_a_cold_zone_have_the_source() -> None:
    braked = _published(_model(_state_with(allocation=0.4, x_f=0.0)))
    inputs = braked.update(_readings(), _ctx(zone=18.0, demand=0.9, output=1.0))
    assert inputs.source_command == pytest.approx(1.0)


def test_the_prediction_respects_what_the_source_water_can_give() -> None:
    model = _published(_model(_state_with(x_f=0.0)))
    ctx = _ctx(zone=19.0)
    model.update(_readings(), ctx)
    full = model._predicted_error(ctx, model.zone_fit(VThermHvacMode_HEAT), (), 1.0)
    model.update(_readings(power_cap=0.3), ctx)
    capped = model._predicted_error(ctx, model.zone_fit(VThermHvacMode_HEAT), (), 1.0)
    assert capped > full


def test_a_published_fit_seeds_smartpi_during_its_learning_phase(monkeypatch) -> None:
    model = _model()
    engine = model._source.fits["heat"]

    def add_sample(*_args, **_kwargs):
        engine.result = _fit(a=0.04, b=0.0012)
        return True

    monkeypatch.setattr(engine, "add_sample", add_sample)
    model.update(_readings(), _ctx(learning_phase=True))
    assert model.pending_seed == (pytest.approx(0.04), pytest.approx(0.0012))
    cooling = _model()
    engine = cooling._source.fits["cool"]
    monkeypatch.setattr(engine, "add_sample", add_sample)
    cooling.update(_readings(), _ctx(learning_phase=True, hvac=VThermHvacMode_COOL))
    assert cooling.pending_seed[0] == pytest.approx(-0.04)


def test_the_fit_its_response_and_the_learning_clock_survive_a_restart() -> None:
    state = DependencyState()
    model = _model(state)
    _run(model, 0.0, 3600.0, learning_phase=True, hvac=VThermHvacMode_COOL)
    _published(model, ZoneFitResult(0.03, 0.001, 0.03, 0.001, 6, 50.0, (), 900.0))
    model.update(_readings(), _ctx(now_s=4000.0, learning_phase=True))

    restarted = _model(state)
    restarted.update(_readings(), _ctx(now_s=50.0, learning_phase=True))
    assert restarted.zone_fit(VThermHvacMode_HEAT).lead_delay_s == 900.0
    assert restarted._source.candidates.current_index == 6
    assert restarted._source.learn_elapsed_s["cool"] == pytest.approx(3600.0)


def test_an_older_single_learning_clock_counts_for_heating() -> None:
    state = DependencyState()
    state.for_item("s1")["learn_elapsed_s"] = 7200.0
    model = _model(state)
    model.update(_readings(active=False), _ctx())
    assert model._source.learn_elapsed_s == {"heat": 7200.0, "cool": 0.0}


def test_fits_learned_with_another_emitter_response_are_dropped() -> None:
    state = DependencyState()
    model = _model(state)
    _published(model)
    model.update(_readings(), _ctx())
    assert state.items["s1"]["response_start"] == [120.0, 180.0]

    state.items["s1"]["response_start"] = [1800.0, 10800.0]
    restarted = _model(state)
    restarted.update(_readings(), _ctx(now_s=60.0))
    assert restarted.zone_fit(VThermHvacMode_HEAT) is None


def test_the_allocation_loop_does_not_integrate_over_a_pause() -> None:
    """Off for hours, then back: the loop goes on from where it was."""
    model = _model(_state_with(allocation=0.3))
    _run(model, 0.0, 1800.0, demand=0.6, step_s=60.0)
    before = model._source.loop.command
    off = model.update(_readings(), _ctx(now_s=1900.0, demand=0.6, hvac=VThermHvacMode_OFF))
    assert off.source_command == 0.0
    back = model.update(_readings(), _ctx(now_s=1900.0 + 8 * 3600.0, demand=0.6))
    assert model._source.loop.command == pytest.approx(before)
    assert back.source_command == pytest.approx(before, abs=0.01)


def test_heating_and_cooling_each_have_their_split_and_time_scale() -> None:
    model = _model(_state_with(a_f=0.03, cool_a_f=0.02, allocation=0.6))
    model._update_source_heat(None, _ctx())  # resume the saved state
    model._source.fits["heat"].result = replace(model._source.fits["heat"].result, candidate=9)
    model._source.fits["cool"].result = replace(model._source.fits["cool"].result, candidate=2)
    model.update(_readings(), _ctx(demand=0.6))
    assert model._source.candidates.current_index == 9
    assert model._source.loop.command == pytest.approx(0.6, abs=0.01)

    model.update(_readings(), _ctx(now_s=60.0, demand=0.6, hvac=VThermHvacMode_COOL))
    assert model._source.candidates.current_index == 2
    assert model._source.loop.command == 0.0


def test_the_loop_speed_follows_the_time_scale_in_use() -> None:
    model = _model(_state_with(a_f=0.03))
    model._update_source_heat(None, _ctx())  # resume the saved state
    model._source.fits["heat"].result = replace(model._source.fits["heat"].result, candidate=9)
    model.update(_readings(), _ctx(demand=0.6))
    slow = model._source.candidates.candidates[9]
    assert model._source.loop.timing.time_constant_s == pytest.approx(
        max(3600.0, 4.0 * slow.settle_s)
    )


def test_out_of_the_learning_blocks_the_loop_starts_from_the_heat_given() -> None:
    model = _model()
    _run(model, 0.0, 3 * 3600.0, readings=_readings(cycles=[_cycle(-600.0)]), learning_phase=True)
    x_f = model._source.x_f
    assert x_f > 0.0
    model.update(_readings(), _ctx(now_s=3 * 3600.0 + 600.0, demand=0.5))
    assert model._source.loop.command == pytest.approx(x_f, abs=0.05)


def test_a_known_fit_seeds_smartpi_whenever_it_learns_again() -> None:
    """After a change of HVAC mode SmartPI learns again; the mode's fit is known: it starts from it."""
    model = _model(_state_with(cool_a_f=0.02))
    model.update(_readings(), _ctx(learning_phase=True, hvac=VThermHvacMode_COOL))
    assert model.pending_seed == (pytest.approx(-0.03), pytest.approx(0.001))


def test_only_a_reliable_fitted_exchange_replaces_the_neighbour_s_own() -> None:
    model = _model()
    good = _fit(k=(("hall", 0.004),))
    good = replace(good, k_se=(("hall", 0.0004),))
    assert model._neighbour_k("hall", good) == 0.004
    assert model._neighbour_k("hall", replace(good, k_se=(("hall", 0.002),))) is None
    assert model._neighbour_k("hall", replace(good, k=(("hall", 0.5),), k_se=(("hall", 0.01),))) is None
    assert model._neighbour_k("hall", replace(good, k=(("hall", -0.004),))) is None


def test_the_brake_counts_the_lead_at_most_at_its_power() -> None:
    """On water the lead's full command gives its water factor: it cannot take the whole load back."""
    model = _known(_published(_model(_state_with(allocation=0.6, x_f=0.6))))
    ctx = _ctx(demand=0.6)
    model.update(_readings(), ctx)
    fit = model.zone_fit(VThermHvacMode_HEAT)
    full_power = model._predicted_error(ctx, fit, (), 0.0)
    weak = model._predicted_error(replace(ctx, lead_power_cap=0.3), fit, (), 0.0)
    assert weak > full_power


def _known(model: DependencyModel) -> DependencyModel:
    """The published fit's heat is fully in the control law."""
    model._known_since[model._source.key(VThermHvacMode_HEAT)] = -math.inf
    return model


def test_the_brake_lets_the_lead_hand_over_its_load() -> None:
    """The lead's feed-forward takes off what the source's known heat adds: the source may take
    the lead's load as long as the lead has some left."""
    model = _known(_published(_model(_state_with(allocation=0.6, x_f=0.2))))
    ctx = _ctx(demand=0.5)
    model.update(_readings(), ctx)
    fit = model.zone_fit(VThermHvacMode_HEAT)

    # From 0.2 to 0.6 of the source the lead backs off by as much, without overshoot.
    assert model._predicted_error(ctx, fit, (), 0.6) == pytest.approx(model._predicted_error(ctx, fit, (), 0.2), abs=0.02)
    assert model._predictive_command(ctx, fit, (), 0.6) >= 0.6
    # Beyond what the lead can hand over the zone would overshoot: the brake holds the source.
    assert model._predictive_command(ctx, fit, (), 0.6) < 1.0


def test_a_zone_too_warm_from_the_lead_alone_keeps_its_source() -> None:
    """The brake keeps the source from adding to an overshoot, not from working at all."""
    model = _known(_published(_model(_state_with(allocation=0.4, x_f=0.4))))
    ctx = _ctx(zone=20.15, demand=0.6)
    model.update(_readings(), ctx)
    fit = model.zone_fit(VThermHvacMode_HEAT)

    assert model._predicted_error(ctx, fit, (), 0.0) < 0.0
    assert model._predictive_command(ctx, fit, (), 0.4) >= 0.4


def test_while_the_brake_holds_the_source_its_allocation_does_not_wind_up() -> None:
    model = _published(_model(_state_with(allocation=0.3, x_f=1.0)))
    model.update(_readings(), _ctx(zone=19.9, demand=0.9))
    assert model._source.braked
    held = model._source.loop.command
    model.update(_readings(), _ctx(now_s=3600.0, zone=19.9, demand=0.9))
    assert model._source.loop.command == pytest.approx(held)


def _recovering(zone: float, lead_output: float = 1.0, x_f: float = 0.6):
    model = _known(_published(_model(_state_with(allocation=0.6, x_f=x_f))))
    model.update(_readings(), _ctx(demand=0.3))
    inputs = model.update(_readings(), replace(_ctx(now_s=60.0, zone=zone, demand=0.3, output=lead_output)))
    return model, inputs


def test_a_recovering_zone_leaves_the_source_s_heat_out_of_the_lead_s_feed_forward() -> None:
    """Far below its setpoint the lead works as if alone; near it, the heat is counted again."""
    model, cold = _recovering(zone=18.0)
    assert cold.source["recovery_boost"] is True
    assert cold.heats == ()

    _, near = _recovering(zone=19.9)
    assert near.source["recovery_boost"] is False
    assert near.heats[0].rate > 0.0


def test_the_boost_ends_before_the_lead_s_heat_on_the_way_overshoots() -> None:
    """Rising fast, the zone reaches the band within the lead's delay plus a cycle: no boost."""
    # a 0.03 at full, x_f 0.6 * a_f 0.03, losses 0.001 * 13.5: about 0.035 degC/min, 0.7 degC in 20 min.
    _, fast = _recovering(zone=19.5, lead_output=1.0)
    assert fast.source["recovery_boost"] is False
    _, slow = _recovering(zone=19.5, lead_output=0.0, x_f=0.0)
    assert slow.source["recovery_boost"] is True


def test_while_the_lead_recovers_the_brake_counts_it_handing_over_afterwards() -> None:
    """The lead's demand then holds the source's whole heat: the brake does not take it for good."""
    model, _ = _recovering(zone=18.0)
    ctx = _ctx(now_s=120.0, zone=18.0, demand=0.8)
    fit = model.zone_fit(VThermHvacMode_HEAT)
    boosted = model._predicted_error(ctx, fit, (), 1.0)
    model._source.boost = False
    counted = model._predicted_error(ctx, fit, (), 1.0)
    assert boosted > counted
