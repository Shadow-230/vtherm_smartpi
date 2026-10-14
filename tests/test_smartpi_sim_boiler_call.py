"""Simulated zone whose boiler is fired by the other zones only part of the day.

The zone's radiator (2000 W) alone stays under the boiler's start threshold
(2500 W); the other zones (3000 W) call except in a window of the day, when the
zone calls for heat that does not come. Told that the boiler is not called,
SmartPI learns nothing that needs heat there (how the zone cools, where its own
governance lets it learn); not told, it takes its command for heat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE
from custom_components.vtherm_smartpi.smartpi.boiler_call import MODE_HELD
from sim.boiler import SimBoiler
from sim.runner import Simulation, ZoneSpec
from sim.weather import Weather


@dataclass
class SharedBoiler(SimBoiler):
    """Fires while the zone's power and the other zones' reach the threshold; its water
    warms towards ``fire_c`` while firing and cools towards ``idle_c`` otherwise."""

    device_w: float = 2000.0
    threshold_w: float = 2500.0
    others_w: float = 3000.0
    others_off_h: tuple[float, float] = (2.0, 8.0)
    fire_c: float = 60.0
    # Without flow the circuit's water ends below the room: its emitter then gives nothing.
    idle_c: float = 12.0
    tau_up_min: float = 4.0
    tau_down_min: float = 20.0
    actuator: object = None
    firing: bool = False
    _water: float | None = None
    _t: float | None = None

    def circuit_c(self, t_s, outdoor_c):  # noqa: ARG002
        if self._t is None:
            self._water, self._t = self.idle_c, t_s
        if t_s > self._t:
            target = self.fire_c if self.firing else self.idle_c
            tau = self.tau_up_min if self.firing else self.tau_down_min
            self._water = target + (self._water - target) * math.exp(-(t_s - self._t) / 60.0 / tau)
            self._t = t_s
        opening = self.actuator.history.value_at(t_s) if self.actuator is not None else 0.0
        hour = (t_s / 3600.0) % 24.0
        others = 0.0 if self.others_off_h[0] <= hour < self.others_off_h[1] else self.others_w
        self.firing = opening * self.device_w + others >= self.threshold_w
        return self._water

    def flow_c(self, t_s, outdoor_c):
        return self.circuit_c(t_s, outdoor_c)


class _SmartBoiler:
    """Smart Boiler's water data for the zone, with or without the boiler's call."""

    def __init__(self, sim, boiler, with_call: bool):
        self.sim, self.boiler, self.with_call = sim, boiler, with_call

    def zone_water(self, entity_id):  # noqa: ARG002
        water = self.boiler.flow_c(self.sim.clock.elapsed_s, 3.0)
        data = {
            "supply_temperature": round(water, 1),
            "supply_reason": None,
            "heat_available": self.boiler.firing,
            "heat_reason": None if self.boiler.firing else "flame_off",
        }
        if self.with_call:
            data["boiler_called"] = self.boiler.firing
            data["call_reason"] = None if self.boiler.firing else "no_demand"
        return data


async def _run(monkeypatch, a, b, off_h, vtherm=False, smart_boiler=None):
    boiler = SharedBoiler(others_off_h=off_h)
    spec = ZoneSpec(
        a=a,
        b=b,
        valve=True,
        sensor_resolution_c=0.01,
        boiler=boiler,
        emitter="radiator",
        options={CONF_SMART_PI_HEATING_TYPE: "radiator"},
    )
    sim = Simulation(monkeypatch, spec, Weather(base_c=3.0, daily_amplitude_c=2.0))
    boiler.actuator = sim.actuator
    if vtherm:
        sim.thermostat.is_used_by_central_boiler = True
        monkeypatch.setattr(
            "custom_components.vtherm_smartpi.handler.vtherm_central_boiler_on", lambda hass: boiler.firing
        )
    if smart_boiler is not None:
        sim.thermostat.hass.data["vtherm_smart_boiler_api"] = _SmartBoiler(sim, boiler, smart_boiler)
    held = {"calculations": 0, "a_learned": 0}
    last_a = [sim.algo.est.learn_ok_count_a]

    def watch(sim_):
        count = sim_.algo.est.learn_ok_count_a
        if sim_.algo.boiler_mode is not None:
            held["calculations"] += sim_.algo.boiler_mode == MODE_HELD
            held["a_learned"] += count - last_a[0]
        last_a[0] = count
        return False

    await sim.run_for(96.0, stop=watch)
    held["dead_time_min"] = (sim.algo.dt_est.deadtime_heat_s or 0.0) / 60.0
    return sim.algo.est.a / a, sim.algo.est.b / b, held


async def test_told_that_the_boiler_is_not_called_smartpi_keeps_its_model(monkeypatch):
    """VT's central boiler, no water data: a night without the boiler under a calling zone."""
    with monkeypatch.context() as patched:
        a_blind, _b, _held = await _run(patched, 0.04, 0.0012, (2.0, 8.0))
    a_told, b_told, held = await _run(monkeypatch, 0.04, 0.0012, (2.0, 8.0), vtherm=True)

    # Not told, SmartPI took its open valve for heat and lost its emitter's power.
    assert a_blind < 0.5
    assert 0.85 < a_told < 1.2
    assert 0.95 < b_told < 1.05
    # Nothing that needs heat was learned under the boiler that is not called, and the
    # dead time stays the valve's (10 min and the sensor): the boiler's start is not timed.
    assert held["calculations"] > 0
    assert held["a_learned"] == 0
    assert 9.0 < held["dead_time_min"] < 13.0


async def test_smart_boiler_s_call_with_water_data(monkeypatch):
    """Smart Boiler's call over its water data: the zone calls with cold water too (its
    device's command, not its power, says so), and nothing that needs heat is learned."""
    a_told, b_told, held = await _run(monkeypatch, 0.06, 0.001, (10.0, 16.0), smart_boiler=True)

    assert 0.95 < b_told < 1.05
    assert 0.9 < a_told < 1.15
    assert held["calculations"] > 0
    assert held["a_learned"] == 0
