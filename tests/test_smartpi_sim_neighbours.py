"""Neighbouring zones in the simulator (scenarios N1-N7).

The zone exchanges heat with neighbours at prescribed temperatures. SmartPI
gets the neighbour readings through the dependency layer, as from the handler,
and learns the exchange itself.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_COOL

from sim.metrics import identification
from sim.runner import SimNeighbour, Simulation, ZoneSpec
from sim.weather import Weather

K = 0.002
OUTDOOR = Weather(base_c=3.0, daily_amplitude_c=3.0)


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
    mild enough for them.
    """
    k = 0.0015
    zone = _zone(SimNeighbour("hall", 20.0, k, mode="always", steps=((30.0, 4.0), (66.0, -4.0))))
    errors = {}
    for layer in (True, False):
        with monkeypatch.context() as patch:
            sim = Simulation(patch, zone, Weather(base_c=5.0, daily_amplitude_c=3.0), dependency_layer=layer)
            await sim.run_for(66.0)
            if layer:
                assert _item(sim, "hall")["k"] == pytest.approx(k, rel=0.2)
                assert sim.algo._known_inputs.loss_coefficient == pytest.approx(0.001, rel=0.2)
                assert sim.algo.control_b == pytest.approx(0.001 + k, rel=0.2)
            await sim.run_for(12.0)
            errors[layer] = _mean_error(sim, 66.0, 78.0)

    assert errors[True] < errors[False]


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
    await sim.run_for(31.5)
    samples_before = _item(sim, "hall")["k_samples"]
    await sim.run_for(1.0)
    heating_s = sim.actuator.history.on_seconds(31.5 * 3600.0 + 120.0, 32.5 * 3600.0)
    assert _item(sim, "hall")["k_samples"] == samples_before
    await sim.run_for(4.0)

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
