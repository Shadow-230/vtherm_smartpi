"""Known model inputs inside SmartPI: substitution, learning correction, gate, state, diagnostics.

The scenarios run the real handler and algorithm in the simulator. A zone
exchanges heat with a neighbour held at a fixed temperature; the dependency
layer is replaced by a provider handing the exchange to SmartPI directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.vtherm_smartpi import algo as algo_module
from custom_components.vtherm_smartpi.const import HVAC_OFF_REASON_WINDOW_DETECTION
from custom_components.vtherm_smartpi.hvac_mode import (
    VThermHvacMode_COOL,
    VThermHvacMode_HEAT,
    VThermHvacMode_OFF,
)
from custom_components.vtherm_smartpi.smartpi.const import SmartPICalibrationResult, clamp
from custom_components.vtherm_smartpi.smartpi.diagnostics import build_published_diagnostics
from custom_components.vtherm_smartpi.smartpi.effective_model import (
    ExchangeInput,
    KnownInputs,
    compose,
)

from sim.metrics import identification, learning_finished
from sim.runner import FixedNeighbour, Simulation, ZoneSpec
from sim.weather import Weather
from tests.helpers import force_smartpi_stable_mode

K = 0.001
NEIGHBOUR_C = 24.0
OUTDOOR = Weather(base_c=0.0)
STATUS = ({"id": "n1", "kind": "neighbour", "entity": "climate.hall"},)


def _zone(**overrides) -> ZoneSpec:
    values = dict(
        a=0.03,
        b=0.001,
        deadtime_min=10.0,
        sensor_resolution_c=0.01,
        neighbours=(FixedNeighbour("hall", NEIGHBOUR_C, K),),
    )
    values.update(overrides)
    return ZoneSpec(**values)


def _exchange(_sim=None) -> KnownInputs:
    return KnownInputs(exchanges=(ExchangeInput("n1", K, NEIGHBOUR_C),), status=STATUS)


class _Switchable:
    """Provider whose inputs a test changes while the simulation runs."""

    def __init__(self) -> None:
        self.inputs: KnownInputs | None = None

    def __call__(self, _sim) -> KnownInputs | None:
        return self.inputs


async def _learned(monkeypatch, zone: ZoneSpec, provider) -> Simulation:
    sim = Simulation(monkeypatch, zone, OUTDOOR, known_inputs=provider)
    await sim.run_for(24.0, stop=learning_finished)
    assert learning_finished(sim)
    return sim


async def test_known_exchange_corrects_the_learning(monkeypatch) -> None:
    """With the exchange known, SmartPI learns the true b; without it, b absorbs the neighbour."""
    with monkeypatch.context() as patch:
        blind = await _learned(patch, _zone(), provider=None)
        blind_b = identification(blind).b_ratio

    informed = await _learned(monkeypatch, _zone(), provider=_exchange)
    result = identification(informed)

    # The warmer neighbour lowers the apparent loss by k*(T_j - T)/(T - T_ext), about 20 %.
    assert blind_b < 0.9
    assert result.b_ratio == pytest.approx(1.0, abs=0.05)
    assert result.a_ratio == pytest.approx(1.0, abs=0.05)
    assert informed.algo.control_b == pytest.approx(informed.algo.est.b + K)


async def test_substitution_reaches_every_control_component(monkeypatch) -> None:
    """Feed-forward, FF3, trajectory, twin, near-band and gains use (b_eff, T_ref); learning stays raw."""
    provider = _Switchable()
    sim = await _learned(monkeypatch, _zone(), provider)
    provider.inputs = _exchange()
    algo = sim.algo
    seen: dict[str, list] = {}
    # The model is composed once per calculation, from the b SmartPI has at its start; a
    # learning step later in the same calculation changes est.b for the next one only.
    tick_b = [algo.est.b]
    calculate = algo.calculate

    def calculating(*args, **kwargs):
        tick_b[0] = algo.est.b
        return calculate(*args, **kwargs)

    monkeypatch.setattr(algo, "calculate", calculating)

    def expected():
        return compose(tick_b[0], sim.thermostat.current_outdoor_temperature, provider.inputs)

    def spy(name, original, extract):
        def wrapper(*args, **kwargs):
            tau_min = algo.est.tau_reliability().tau_min
            seen.setdefault(name, []).append((extract(*args, **kwargs), expected(), tau_min))
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(algo_module, "compute_ff", spy("ff1", algo_module.compute_ff, lambda **kw: (kw["k_ff"], kw["ext_temp"])))
    monkeypatch.setattr(algo_module, "compute_ff3", spy("ff3", algo_module.compute_ff3, lambda **kw: (kw["b"], kw["ext_temp"])))
    monkeypatch.setattr(algo.sp_mgr, "filter_setpoint", spy("setpoint", algo.sp_mgr.filter_setpoint, lambda **kw: (kw["b"], kw["ext_current_temp"])))
    monkeypatch.setattr(algo.twin, "update_with_eta", spy("twin", algo.twin.update_with_eta, lambda **kw: (kw["b"], kw["text"])))
    monkeypatch.setattr(algo.deadband_mgr, "update", spy("near_band", algo.deadband_mgr.update, lambda **kw: (kw["estimator"].b, kw["ext_temp"])))
    monkeypatch.setattr(algo.gain_scheduler, "calculate", spy("gains", algo.gain_scheduler.calculate, lambda **kw: (kw["tau_min"], None)))
    monkeypatch.setattr(algo, "update_learning", spy("learning", algo.update_learning, lambda **kw: (kw["known_rate"], kw["ext_temp"])))
    trim = algo._fftrim_observer
    monkeypatch.setattr(trim, "try_complete_window", spy("fftrim", trim.try_complete_window, lambda **kw: (kw["b"], None)))

    await sim.run_for(2.0)

    assert {"ff1", "ff3", "setpoint", "twin", "near_band", "gains", "learning", "fftrim"} <= set(seen)
    for name in ("ff3", "setpoint", "twin", "near_band"):
        for (b, ext), model, _tau in seen[name]:
            assert b == pytest.approx(model.b)
            assert ext == pytest.approx(model.ext_temp)
    # FF1 is off (k_ff 0) while SmartPI's time constant is not reliable yet.
    active_ff1 = [entry for entry in seen["ff1"] if entry[0][0] != 0.0]
    assert active_ff1
    for (k_ff, ext), model, _tau in active_ff1:
        assert k_ff == pytest.approx(clamp(model.b / algo.est.a, -3.0, 3.0))
        assert ext == pytest.approx(model.ext_temp)
    for (b, _none), model, _tau in seen["fftrim"]:
        assert b == pytest.approx(model.b)
    # An unreliable time constant is passed on as is (9999 min), with or without inputs.
    reliable_gains = [entry for entry in seen["gains"] if entry[2] < 9999.0]
    assert reliable_gains
    for (tau_min, _none), _model, tau_base in reliable_gains:
        # The learned time constant gains the exchange conductance.
        assert tau_min == pytest.approx(1.0 / (1.0 / tau_base + K))
    for (known_rate, ext), _model, _tau in seen["learning"]:
        assert ext == sim.thermostat.current_outdoor_temperature
        assert known_rate > 0.0


async def test_learning_gate_pauses_learning_and_dead_times(monkeypatch) -> None:
    """While gated, no learning window runs and no dead time is learned; then learning resumes."""
    provider = _Switchable()
    sim = await _learned(monkeypatch, _zone(), provider)
    algo = sim.algo
    calls = {"learning": 0, "deadtime": 0}
    update_learning, deadtime_update = algo.update_learning, algo.dt_est.update

    def count(name, original):
        def wrapper(*args, **kwargs):
            calls[name] += 1
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(algo, "update_learning", count("learning", update_learning))
    monkeypatch.setattr(algo.dt_est, "update", count("deadtime", deadtime_update))

    provider.inputs = KnownInputs(learning_gate="door_open", status=STATUS)
    await sim.run_for(1.0)
    assert calls == {"learning": 0, "deadtime": 0}
    assert not algo.learn_win.active
    assert algo.dt_est.state == "OFF"
    assert not algo.dt_est.tin_history
    assert not algo.dt_est._tin_lp_history
    assert build_published_diagnostics(algo)["dependencies"]["learning_gate"] == "door_open"

    provider.inputs = KnownInputs(status=STATUS)
    await sim.run_for(1.0)
    assert calls["learning"] > 0
    assert calls["deadtime"] > 0


async def test_changed_inputs_invalidate_open_fftrim_windows(monkeypatch) -> None:
    """FF trim evidence gathered under the previous model is discarded when the inputs change."""
    provider = _Switchable()
    sim = await _learned(monkeypatch, _zone(), provider)
    reasons: list[str] = []
    invalidate = sim.algo._invalidate_fftrim_context

    def recording(reason, **kwargs):
        reasons.append(reason)
        return invalidate(reason, **kwargs)

    monkeypatch.setattr(sim.algo, "_invalidate_fftrim_context", recording)
    await sim.run_for(0.5)
    assert "known_inputs_changed" not in reasons

    provider.inputs = _exchange()
    await sim.run_for(0.5)
    assert reasons.count("known_inputs_changed") == 1


async def test_dependency_state_persists_only_when_present(monkeypatch) -> None:
    """The saved state gains a versioned dependencies key only when an item has state."""
    sim = Simulation(monkeypatch, _zone(neighbours=()))
    algo = sim.algo
    assert "dependencies" not in algo.save_state()

    algo.dependency_state.for_item("n1")["k"] = 0.0012
    saved = algo.save_state()
    assert saved["dependencies"]["version"] == 1

    restored = Simulation(monkeypatch, _zone(neighbours=())).algo
    restored.load_state(saved)
    assert restored.dependency_state.items == {"n1": {"k": 0.0012}}

    restored.reset_learning()
    assert not restored.dependency_state

    older = dict(saved)
    del older["dependencies"]
    restored.load_state(older)
    assert not restored.dependency_state


async def test_diagnostics_show_dependencies_only_when_configured(monkeypatch) -> None:
    """Without configured dependencies the published diagnostics are unchanged."""
    plain = Simulation(monkeypatch, _zone())
    await plain.run_for(0.5)
    assert "dependencies" not in build_published_diagnostics(plain.algo)

    with monkeypatch.context() as patch:
        informed = Simulation(patch, _zone(), known_inputs=_exchange)
        await informed.run_for(0.5)
        block = build_published_diagnostics(informed.algo)["dependencies"]

    assert block["inputs_active"] is True
    assert block["b_eff"] == pytest.approx(informed.algo.est.b + K, rel=1e-3)
    assert block["learning_gate"] is None
    assert block["items"] == [dict(STATUS[0])]


async def test_learning_gate_leaves_the_control_episode_untouched(monkeypatch) -> None:
    """Opening a gate pauses learning only; the dead-time hold of the control law is not re-armed."""
    provider = _Switchable()
    sim = await _learned(monkeypatch, _zone(neighbours=()), provider)
    algo = sim.algo
    await sim.run_for(12.0, stop=lambda s: s.algo._t_heat_episode_start is not None)
    episode_start = algo._t_heat_episode_start
    assert episode_start is not None

    provider.inputs = KnownInputs(learning_gate="door_open", status=STATUS)
    await sim.run_for(0.05)

    assert algo._t_heat_episode_start == episode_start


async def test_closing_the_gate_does_not_invent_a_dead_time(monkeypatch) -> None:
    """Heating that starts during a gate is not read as a fresh response when the gate closes."""
    provider = _Switchable()
    provider.inputs = KnownInputs(status=STATUS)
    sim = Simulation(monkeypatch, _zone(neighbours=()), OUTDOOR, known_inputs=provider)
    algo = sim.algo
    await sim.run_for(
        24.0,
        stop=lambda s: len(s.algo.dt_est._history_heat) >= 1 and not s.actuator.is_device_active,
    )
    assert algo.dt_est._history_heat

    provider.inputs = KnownInputs(learning_gate="door_open", status=STATUS)
    await sim.run_for(12.0, stop=lambda s: s.actuator.is_device_active)
    assert sim.actuator.is_device_active
    await sim.run_for(20.0 / 60.0)
    provider.inputs = KnownInputs(status=STATUS)
    await sim.run_for(1.0)

    true_deadtime_s = sim.zone.deadtime_min * 60.0
    assert all(value >= 0.5 * true_deadtime_s for value in algo.dt_est._history_heat)


async def test_a_failing_provider_does_not_stop_regulation(monkeypatch) -> None:
    def failing(_sim):
        raise RuntimeError("registry unavailable")

    sim = Simulation(monkeypatch, _zone(neighbours=()), OUTDOOR, known_inputs=failing)
    await sim.run_for(2.0)

    assert sim.actuator.turn_on_count > 0
    assert sim.algo.control_b == sim.algo.est.b
    assert "dependencies" not in build_published_diagnostics(sim.algo)


@pytest.mark.parametrize(
    ("hvac_mode", "target", "preset", "expected"),
    [
        (VThermHvacMode_HEAT, 20.0, 17.0, 17.0),
        (VThermHvacMode_HEAT, 16.0, 17.0, 16.0),
        (VThermHvacMode_COOL, 24.0, 27.0, 27.0),
        (VThermHvacMode_COOL, 28.0, 27.0, 28.0),
    ],
    ids=["heat_lowers", "heat_never_raises", "cool_raises", "cool_never_lowers"],
)
async def test_neighbour_window_preset_never_asks_for_more_energy(
    monkeypatch, hvac_mode, target, preset, expected
) -> None:
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    algo._known_inputs = KnownInputs(control_action="eco", action_setpoint=preset, status=STATUS)

    assert algo._apply_neighbour_window_action(target, hvac_mode, None) == (expected, hvac_mode, None)


async def test_neighbour_window_reaction_cancels_a_calibration(monkeypatch) -> None:
    """The reaction overrides the calibration run: it ends as cancelled instead of stalling."""
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    algo.calibration_mgr.start_calibration(now=0.0, is_manual=True)
    algo._known_inputs = KnownInputs(control_action="turn_off", status=STATUS)

    target, hvac_mode, off_reason = algo._apply_neighbour_window_action(20.0, VThermHvacMode_HEAT, None)

    assert (target, hvac_mode, off_reason) == (20.0, VThermHvacMode_OFF, HVAC_OFF_REASON_WINDOW_DETECTION)
    assert not algo.calibration_mgr.is_calibrating
    assert algo.calibration_mgr.calibration_result == SmartPICalibrationResult.CANCELLED


async def test_exchange_learning_follows_smartpi_learning_conditions(monkeypatch) -> None:
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo

    def allowed(hvac_mode=VThermHvacMode_HEAT, off_reason=None, power_shedding=False):
        return algo._dependency_learning_allowed(1000.0, hvac_mode, off_reason, power_shedding)

    assert allowed()
    assert allowed(VThermHvacMode_COOL)
    assert not allowed(VThermHvacMode_OFF)
    assert not allowed(off_reason=HVAC_OFF_REASON_WINDOW_DETECTION)
    assert not allowed(power_shedding=True)

    algo.learn_win.set_learning_resume_ts(2000.0)
    assert not allowed()
    algo.learn_win.set_learning_resume_ts(None)

    # A guard cut gives the zone a known power (none): the exchange keeps learning.
    monkeypatch.setattr(algo.guards, "_guard_cut_active", True)
    assert allowed()
    monkeypatch.setattr(algo.guards, "_guard_cut_active", False)

    algo.calibration_mgr.start_calibration(now=0.0, is_manual=True)
    assert not allowed()
    algo.calibration_mgr.reset()

    algo.set_learning_enabled(False)
    assert not allowed()


async def test_gains_use_the_fitted_loss_with_always_neighbours(monkeypatch) -> None:
    """SmartPI's b already holds an always-connected exchange: Ti starts from the loss fitted without it."""
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    k, loss, learned_b = 0.0015, 0.001, 0.0024
    inputs = KnownInputs(exchanges=(ExchangeInput("n1", k, 20.0),), loss_coefficient=loss, status=STATUS)
    algo._known_inputs = inputs
    algo._effective_model = compose(learned_b, 5.0, inputs)
    tau = SimpleNamespace(reliable=True, tau_min=1.0 / learned_b)

    assert algo._control_tau_min(tau) == pytest.approx(1.0 / (loss + k))


async def test_the_fitted_loss_in_the_gains_fades_in_with_its_exchange(monkeypatch) -> None:
    """Halfway through the fade the integral time is still SmartPI's own, which holds the exchange."""
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    k, loss, learned_b = 0.0015, 0.001, 0.0025
    inputs = KnownInputs(
        exchanges=(ExchangeInput("n1", 0.5 * k, 20.0),), loss_coefficient=loss, loss_weight=0.5, status=STATUS
    )
    algo._known_inputs = inputs
    algo._effective_model = compose(learned_b, 5.0, inputs)
    tau = SimpleNamespace(reliable=True, tau_min=1.0 / learned_b)

    assert algo._control_tau_min(tau) == pytest.approx(1.0 / learned_b)


async def test_completed_cycles_report_their_realized_power(monkeypatch) -> None:
    """A switched cycle delivers its energy at full power from its start."""
    sim = Simulation(monkeypatch, _zone(neighbours=()))
    algo = sim.algo
    end = algo_module.time.monotonic()

    await algo.on_cycle_completed(e_eff=0.5, elapsed_ratio=0.5, cycle_duration_min=10.0)

    assert list(algo.dependency_model._coupling._realized) == [
        (end - 300.0, end - 150.0, 1.0),
        (end - 150.0, end, 0.0),
    ]


async def test_the_allocation_follows_the_steady_demand_of_the_lead(monkeypatch) -> None:
    """Feed-forward plus integral: the proportional answer to the current error is left out."""
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    algo.ctl.u_ff, algo.ctl.u_p, algo.ctl.u_i, algo.ctl.u_pi = 0.3, 0.5, 0.1, 0.6

    assert algo._lead_demand() == pytest.approx(0.4)


async def test_a_configured_source_shows_in_the_diagnostics(monkeypatch) -> None:
    from custom_components.vtherm_smartpi.smartpi.dependencies import KIND_SOURCE, DependencyItem
    from custom_components.vtherm_smartpi.smartpi.dependency_inputs import DependencyReadings, SourceObservation
    from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings

    sim = Simulation(monkeypatch, _zone(neighbours=()), OUTDOOR)
    algo = sim.algo
    algo.configure_source(
        SourceSettings.from_item(
            DependencyItem(item_id="s1", kind=KIND_SOURCE, entity="reg", extra={"split_rule": "together"})
        )
    )
    algo.set_known_inputs_provider(
        lambda: DependencyReadings(source=SourceObservation("s1", "climate.floor", active=True))
    )
    await sim.run_for(0.2)

    block = build_published_diagnostics(algo)["dependencies"]
    assert block["second_source"]["rule"] == "together"
    # While SmartPI learns, the additional source works too (learning blocks).
    assert block["second_source"]["mode"] == "learn"
    assert algo.source_command == 1.0
    assert block["second_source"]["zone_fit"]["published"] is False


async def test_a_failing_dependency_layer_gives_the_source_nothing(monkeypatch) -> None:
    """The last command must not be refreshed when the inputs cannot be read."""
    from custom_components.vtherm_smartpi.smartpi.dependencies import KIND_SOURCE, DependencyItem
    from custom_components.vtherm_smartpi.smartpi.dependency_inputs import DependencyReadings, SourceObservation
    from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings

    sim = Simulation(monkeypatch, _zone(neighbours=()), OUTDOOR)
    algo = sim.algo
    algo.configure_source(
        SourceSettings.from_item(DependencyItem(item_id="s1", kind=KIND_SOURCE, entity="reg", extra={"split_rule": "together"}))
    )
    algo.dependency_model._source.command = 0.8
    algo.set_known_inputs_provider(lambda: DependencyReadings(source=SourceObservation("s1", None, active=True)))
    monkeypatch.setattr(algo.dependency_model, "update", lambda readings, ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    await sim.run_for(0.1)

    assert algo.source_command is None


async def test_smartpi_holds_the_zone_fit_and_its_evidence(monkeypatch) -> None:
    """With the zone's fit published, SmartPI's a, b, time constant and feed-forward warm-up come from it."""
    from custom_components.vtherm_smartpi.smartpi.dependencies import KIND_SOURCE, DependencyItem
    from custom_components.vtherm_smartpi.smartpi.source_settings import SourceSettings
    from custom_components.vtherm_smartpi.smartpi.zone_fit import ZoneFitResult

    from helpers import force_smartpi_stable_mode

    algo = Simulation(monkeypatch, _zone(neighbours=()), OUTDOOR).algo
    algo.configure_source(
        SourceSettings.from_item(DependencyItem(item_id="s1", kind=KIND_SOURCE, entity="reg", extra={"split_rule": "together"}))
    )
    force_smartpi_stable_mode(algo)
    algo.est.learn_ok_count = 3
    algo._apply_zone_fit(VThermHvacMode_HEAT)
    assert algo.est.learn_ok_count == 3

    algo.dependency_model._source.fits["heat"].result = ZoneFitResult(0.025, 0.0012, 0.02, 0.001, 4, 50.0)
    algo._apply_zone_fit(VThermHvacMode_HEAT)
    tau = algo.est.tau_reliability()
    assert (algo.est.a, algo.est.b) == (0.025, 0.0012)
    assert tau.reliable
    assert tau.tau_min == pytest.approx(1.0 / 0.0012)
    assert algo.est.learn_ok_count >= algo.ff_warmup_ok_count
    assert algo.est.learn_ok_count_a >= 10

    # In cooling the heating fit does not apply.
    algo.est.a = -0.02
    algo._apply_zone_fit(VThermHvacMode_COOL)
    assert algo.est.a == -0.02


async def test_neighbour_window_preset_applies_without_a_target(monkeypatch) -> None:
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    algo._known_inputs = KnownInputs(control_action="eco", action_setpoint=17.0, status=STATUS)

    assert algo._apply_neighbour_window_action(None, VThermHvacMode_HEAT, None) == (17.0, VThermHvacMode_HEAT, None)


async def test_a_source_command_is_ignored_outside_the_source_role(monkeypatch) -> None:
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    before = algo.on_percent

    algo.set_source_command(0.7)

    assert not algo.is_executive and algo.on_percent == before


async def test_a_lead_that_can_give_nothing_has_no_output_share(monkeypatch) -> None:
    """VTherm's maximum on-percent at 0: the lead's share of what it can give is 0, not a
    division by zero."""
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    algo._max_on_percent = 0.0

    assert algo._lead_output_share() == 0.0


async def test_a_zone_fit_published_after_the_learning_phase_does_not_replace_a_and_b(monkeypatch) -> None:
    """The joint fit seeds a and b only while SmartPI still learns them (hysteresis phase)."""
    algo = Simulation(monkeypatch, _zone(neighbours=())).algo
    force_smartpi_stable_mode(algo)
    a, b = algo.est.a, algo.est.b
    algo.dependency_model.pending_seed = (a * 2.0, b * 2.0)

    algo._apply_zone_fit(VThermHvacMode_HEAT)

    assert (algo.est.a, algo.est.b) == (a, b)
    assert algo.dependency_model.pending_seed is None
