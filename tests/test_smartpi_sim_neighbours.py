"""Neighbouring zones in the simulator (scenarios N1-N7).

The zone exchanges heat with neighbours at prescribed temperatures. SmartPI
gets the neighbour readings through the dependency layer, as from the handler,
and learns the exchange itself.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from custom_components.vtherm_smartpi.hvac_mode import (
    VThermHvacMode_COOL,
    VThermHvacMode_HEAT,
    VThermHvacMode_OFF,
)

from sim.metrics import identification
from sim.runner import SimNeighbour, Simulation, ZoneSpec
from sim.weather import Weather

K = 0.002
OUTDOOR = Weather(base_c=3.0, daily_amplitude_c=3.0)
N3_OUTDOOR = Weather(base_c=5.0, daily_amplitude_c=3.0)
# Further control phases of a scenario, run locally (about 16 s each).
LOCAL = pytest.mark.skipif(
    os.environ.get("SMARTPI_SIM_LOCAL") != "1",
    reason="further control phase, run locally with SMARTPI_SIM_LOCAL=1",
)


def _doors(days: int) -> tuple[tuple[float, float], ...]:
    """Doors open three times a day."""
    return tuple(
        (24.0 * day + start, 24.0 * day + end)
        for day in range(days)
        for start, end in ((7.0, 9.0), (12.0, 13.5), (18.0, 22.0))
    )


def _zone(*neighbours: SimNeighbour, **overrides) -> ZoneSpec:
    values = dict(a=0.03, b=0.001, deadtime_min=10.0, sensor_resolution_c=0.01, neighbours=neighbours)
    values.update(overrides)
    return ZoneSpec(**values)


def _item(sim: Simulation, key: str) -> dict:
    return next(item for item in sim.algo.dependency_diagnostics()["items"] if item["id"] == key)


def _mean_error(sim: Simulation, start_h: float, end_h: float) -> float:
    points = [p for p in sim.trace if start_h <= p.t_h < end_h]
    return sum(abs(p.zone_c - sim.zone.setpoint_c) for p in points) / len(points)


async def _traces(monkeypatch, zone: ZoneSpec, hours: float) -> tuple[list, list]:
    """Run with the dependency layer and with the substitution switched off."""
    traces = []
    for enabled in (True, False):
        with monkeypatch.context() as patch:
            sim = Simulation(patch, zone, OUTDOOR, dependency_layer=True)
            sim.algo._known_inputs_enabled = enabled
            await sim.run_for(hours)
            traces.append(sim.trace)
    return traces[0], traces[1]


@pytest.mark.parametrize(
    "neighbour",
    [
        SimNeighbour("hall", 16.0, K, mode="none"),
        SimNeighbour("hall", 16.0, K, mode="always", available=False),
    ],
    ids=["N1_ignored_neighbour", "N4_unavailable_neighbour"],
)
async def test_neighbour_without_influence_changes_nothing(monkeypatch, neighbour) -> None:
    """An ignored or unavailable neighbour leaves SmartPI exactly as without dependencies."""
    with_layer, without = await _traces(monkeypatch, _zone(neighbour), hours=24.0)

    assert with_layer == without


async def test_n2_door_neighbour_is_learned_without_disturbing_the_model(monkeypatch) -> None:
    """k is learned while the doors are open; a, b and the dead time learn only while closed."""
    sim = Simulation(
        monkeypatch,
        _zone(SimNeighbour("hall", 16.0, K, mode="door", doors_open_h=_doors(3))),
        OUTDOOR,
        dependency_layer=True,
    )
    dead_time_updates_with_doors_open = 0
    update = sim.algo.dt_est.update

    def counting(*args, **kwargs):
        nonlocal dead_time_updates_with_doors_open
        if sim.zone.neighbours[0].observation(sim.clock.elapsed_s).door_open:
            dead_time_updates_with_doors_open += 1
        return update(*args, **kwargs)

    monkeypatch.setattr(sim.algo.dt_est, "update", counting)
    await sim.run_for(60.0)

    result = identification(sim)
    assert _item(sim, "hall")["k"] == pytest.approx(K, rel=0.2)
    assert result.a_ratio == pytest.approx(1.0, abs=0.05)
    assert result.b_ratio == pytest.approx(1.0, abs=0.05)
    assert dead_time_updates_with_doors_open == 0


async def test_n3_always_neighbour_is_separated_and_helps_after_a_step(monkeypatch) -> None:
    """b and k are separated once the neighbour moves on its own; its next step is handled better.

    The feed-forward that uses the effective model is active only once SmartPI's
    time constant is reliable, which needs passive cooling periods: the zone is
    mild enough for them. No k is published from the first hours after the
    step, which share the same errors of SmartPI's own model.
    """
    k = 0.0015
    zone = _zone(SimNeighbour("hall", 20.0, k, mode="always", steps=((30.0, 4.0), (66.0, -4.0))))
    errors = {}
    for layer in (True, False):
        with monkeypatch.context() as patch:
            sim = Simulation(patch, zone, N3_OUTDOOR, dependency_layer=layer)
            if layer:
                await sim.run_for(42.0)
                assert _item(sim, "hall")["k"] is None
                await sim.run_for(24.0)
                assert _item(sim, "hall")["k"] == pytest.approx(k, rel=0.1)
                assert sim.algo._known_inputs.loss_coefficient == pytest.approx(0.001, rel=0.1)
                assert sim.algo.control_b == pytest.approx(0.001 + k, rel=0.1)
            else:
                await sim.run_for(66.0)
            await sim.run_for(12.0)
            errors[layer] = _mean_error(sim, 66.0, 78.0)

    assert errors[True] < errors[False]


async def test_n3_coarse_sensor_publishes_nothing_doubtful(monkeypatch) -> None:
    """With a 0.1 degC sensor the same zone cannot separate b from k: nothing is published."""
    k = 0.0015
    zone = _zone(
        SimNeighbour("hall", 20.0, k, mode="always", steps=((30.0, 4.0),)), sensor_resolution_c=0.1
    )
    sim = Simulation(monkeypatch, zone, N3_OUTDOOR, dependency_layer=True)
    await sim.run_for(66.0)

    item = _item(sim, "hall")
    if item["k"] is None:
        assert item["indistinguishable"]
        assert sim.algo.control_b == sim.algo.est.b
    else:
        assert item["k"] == pytest.approx(k, rel=0.2)


async def test_n5_cooling_with_a_neighbour(monkeypatch) -> None:
    """In cooling, the learned exchange is positive and the zone stays at its setpoint."""
    sim = Simulation(
        monkeypatch,
        _zone(
            SimNeighbour("hall", 28.0, K, mode="door", doors_open_h=_doors(4)),
            a=-0.03,
            initial_c=25.0,
            setpoint_c=24.0,
            sensor_resolution_c=0.1,
        ),
        Weather(base_c=32.0, daily_amplitude_c=3.0),
        dependency_layer=True,
    )
    sim.thermostat.vtherm_hvac_mode = VThermHvacMode_COOL
    await sim.run_for(90.0)

    assert _item(sim, "hall")["k"] == pytest.approx(K, rel=0.25)
    assert _mean_error(sim, 60.0, 90.0) < 0.3


@pytest.mark.parametrize("action", ["nothing", "turn_off", "frost", "eco"])
async def test_n6_window_at_a_connected_neighbour(monkeypatch, action) -> None:
    """The chosen reaction applies while the door is open; learning pauses; the zone comes back."""
    neighbour = SimNeighbour(
        "hall", 16.0, K, mode="door", doors_open_h=_doors(2), windows_open_h=((31.5, 32.5),)
    )
    original = SimNeighbour.observation

    def with_action(self, t_s):
        observation = original(self, t_s)
        return replace(observation, item=replace(observation.item, neighbour_window_action=action))

    monkeypatch.setattr(SimNeighbour, "observation", with_action)
    sim = Simulation(monkeypatch, _zone(neighbour, sensor_resolution_c=0.1), OUTDOOR, dependency_layer=True)
    applied: list[tuple] = []
    react = sim.algo._apply_neighbour_window_action

    def recording(target_temp, hvac_mode, off_reason):
        result = react(target_temp, hvac_mode, off_reason)
        if 31.6 <= sim.clock.elapsed_s / 3600.0 < 32.4:
            applied.append(result[:2])
        return result

    monkeypatch.setattr(sim.algo, "_apply_neighbour_window_action", recording)
    await sim.run_for(31.5)
    samples_before = _item(sim, "hall")["k_samples"]
    await sim.run_for(1.0)
    heating_s = sim.actuator.history.on_seconds(31.5 * 3600.0 + 120.0, 32.5 * 3600.0)
    assert _item(sim, "hall")["k_samples"] == samples_before
    await sim.run_for(4.0)

    expected = {
        "nothing": (sim.zone.setpoint_c, VThermHvacMode_HEAT),
        "turn_off": (sim.zone.setpoint_c, VThermHvacMode_OFF),
        "frost": (sim.zone.presets["frost_temp"], VThermHvacMode_HEAT),
        "eco": (sim.zone.presets["eco_temp"], VThermHvacMode_HEAT),
    }[action]
    assert applied and all(result == expected for result in applied)
    if action == "nothing":
        assert heating_s > 1200.0
    else:
        assert heating_s < 300.0
    assert max(p.zone_c for p in sim.trace if p.t_h >= 32.5) < sim.zone.setpoint_c + 0.6


async def test_n7_zone_between_two_always_neighbours(monkeypatch) -> None:
    """Two always-connected neighbours are separated once both have moved, or nothing is published."""
    sim = Simulation(
        monkeypatch,
        _zone(
            SimNeighbour("left", 16.0, 0.0015, mode="always", steps=((30.0, 5.0),)),
            SimNeighbour("right", 22.0, 0.001, mode="always", steps=((50.0, -5.0),)),
        ),
        OUTDOOR,
        dependency_layer=True,
    )
    await sim.run_for(80.0)

    for key, truth in (("left", 0.0015), ("right", 0.001)):
        item = _item(sim, key)
        if item["k"] is None:
            assert item["indistinguishable"]
        else:
            assert item["k"] == pytest.approx(truth, rel=0.35)


@pytest.mark.parametrize("phase_s", [0.0, pytest.param(170.0, marks=LOCAL), pytest.param(340.0, marks=LOCAL)])
async def test_landing_with_a_neighbour_exchange(monkeypatch, phase_s) -> None:
    """With a learned always-connected neighbour, the reference governor brings the zone back
    from a night at 17 degC as the setpoint filter did before it.

    Two mornings after the N3 learning, in three control phases (two of them locally). Before the reference
    governor (dev2 9868b33) and with it: 2.96-3.04 h and 3.26-3.27 h to 19.9 degC,
    overshoot 0.23-0.24 degC and 0.15 degC. The bounds leave 0.06 degC and 0.3 h of margin.
    """
    zone = _zone(
        SimNeighbour("hall", 20.0, 0.0015, mode="always", steps=((30.0, 4.0), (66.0, -4.0))),
        setpoint_c=20.0,
        control_phase_s=phase_s,
    )
    sim = Simulation(monkeypatch, zone, N3_OUTDOOR, dependency_layer=True)
    await sim.run_for(70.0)
    assert _item(sim, "hall")["k"] is not None

    async def setpoint(value: float) -> None:
        sim.thermostat.target_temperature = value
        sim.zone.setpoint_c = value
        await sim.thermostat.async_control_heating(force=True)

    for _night in range(2):
        await setpoint(17.0)
        await sim.run_for(8.0)
        start_h = sim.clock.elapsed_s / 3600.0
        await setpoint(20.0)
        await sim.run_for(8.0)
        morning = [p for p in sim.trace if p.t_h >= start_h]
        reach_h = next(p.t_h - start_h for p in morning if p.zone_c >= 19.9)
        assert reach_h < 3.6
        assert max(p.zone_c for p in morning) - 20.0 < 0.30
        await sim.run_for(8.0)
