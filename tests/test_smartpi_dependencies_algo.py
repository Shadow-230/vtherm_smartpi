"""Known model inputs inside SmartPI: substitution, learning correction, gate, state, diagnostics.

The scenarios run the real handler and algorithm in the simulator. A zone
exchanges heat with a neighbour held at a fixed temperature; the dependency
layer is replaced by a provider handing the exchange to SmartPI directly.
"""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi import algo as algo_module
from custom_components.vtherm_smartpi.smartpi.const import clamp
from custom_components.vtherm_smartpi.smartpi.diagnostics import build_published_diagnostics
from custom_components.vtherm_smartpi.smartpi.effective_model import (
    ExchangeInput,
    KnownInputs,
    compose,
)

from sim.metrics import identification, learning_finished
from sim.runner import FixedNeighbour, Simulation, ZoneSpec
from sim.weather import Weather

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

    def expected():
        return compose(algo.est.b, sim.thermostat.current_outdoor_temperature, provider.inputs)

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
    for (k_ff, ext), model, _tau in seen["ff1"]:
        assert k_ff == pytest.approx(clamp(model.b / algo.est.a, -3.0, 3.0))
        assert ext == pytest.approx(model.ext_temp)
    for (b, _none), model, _tau in seen["fftrim"]:
        assert b == pytest.approx(model.b)
    for (tau_min, _none), _model, tau_base in seen["gains"]:
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
