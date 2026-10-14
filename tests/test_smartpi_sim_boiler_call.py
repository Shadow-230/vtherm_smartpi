"""Simulated zone whose boiler is fired by the other zones only part of the day.

The zone's radiator (2000 W) alone stays under the boiler's start threshold
(2500 W); the other zones (3000 W) call except in a window of the day, when the
zone calls for heat that does not come. Told that the boiler is not called
(from VT's central boiler, an entity or a provider), SmartPI learns nothing that
needs heat there (how the zone cools, where its own governance lets it learn);
not told, it takes its command for heat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from custom_components.vtherm_smartpi import heating_inputs
from custom_components.vtherm_smartpi.const import (
    CONF_SMART_PI_BOILER_CALL_SOURCE,
    CONF_SMART_PI_BOILER_CALLED_ENTITY,
    CONF_SMART_PI_BOILER_SIGNALS,
    CONF_SMART_PI_HEATING_TYPE,
    CONF_SMART_PI_WATER_SOURCE,
)
from custom_components.vtherm_smartpi.heating_inputs import PROVIDERS_KEY
from custom_components.vtherm_smartpi.smartpi.boiler_call import MODE_HELD
from sim.boiler import SimBoiler, SimState
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


class _Provider:
    """A provider of SmartPI's contract: the boiler's call, with or without the water data."""

    name = "plant"
    version = 1

    def __init__(self, sim, boiler, with_water: bool):
        self.sim, self.boiler, self.with_water = sim, boiler, with_water

    def heating_inputs(self, entity_id):  # noqa: ARG002
        data = {
            "boiler_called": self.boiler.firing,
            "call_reason": None if self.boiler.firing else "below_threshold",
        }
        if self.with_water:
            water = self.boiler.flow_c(self.sim.clock.elapsed_s, 3.0)
            data.update(
                supply_temperature=round(water, 1),
                supply_reason=None,
                heat_available=self.boiler.firing,
                heat_reason=None if self.boiler.firing else "flame_off",
            )
        return data


def _vtherm_central_boiler(monkeypatch, sim, boiler):
    """VT's central boiler as its live entities: on while firing, the active power (W) against
    the power threshold (the count criterion off)."""
    entity_ids = {
        "central_boiler_state": "binary_sensor.central_boiler",
        "nb_device_active_boiler": "sensor.nb_device_active_boiler",
        "total_power_active_boiler": "sensor.total_power_active_boiler",
        "boiler_activation_threshold": "number.boiler_activation_threshold",
        "boiler_power_activation_threshold": "number.boiler_power_activation_threshold",
    }
    monkeypatch.setattr(heating_inputs, "_vt_entity_id", lambda hass, domain, unique_id: entity_ids.get(unique_id))
    sim.thermostat.is_used_by_central_boiler = True

    def power():
        t_s = sim.clock.elapsed_s
        hour = (t_s / 3600.0) % 24.0
        others = 0.0 if boiler.others_off_h[0] <= hour < boiler.others_off_h[1] else boiler.others_w
        return sim.actuator.history.value_at(t_s) * boiler.device_w + others

    states = sim.states
    states.set("binary_sensor.central_boiler", lambda: SimState("on" if boiler.firing else "off"))
    states.set("sensor.nb_device_active_boiler", lambda: SimState(str(int(power() > 0.0))))
    states.set("number.boiler_activation_threshold", lambda: SimState("0"))
    states.set("sensor.total_power_active_boiler", lambda: SimState(f"{power():.0f}", {"unit_of_measurement": "W"}))
    states.set(
        "number.boiler_power_activation_threshold",
        lambda: SimState(f"{boiler.threshold_w:.0f}", {"unit_of_measurement": "W"}),
    )


def _called_entity(sim, boiler):
    """The boiler's call as a binary sensor, the thermostat's own signal."""
    sim.states.set("binary_sensor.boiler_called", lambda: SimState("on" if boiler.firing else "off"))
    sim.thermostat.smartpi_options.update(
        {CONF_SMART_PI_BOILER_SIGNALS: "own", CONF_SMART_PI_BOILER_CALLED_ENTITY: "binary_sensor.boiler_called"}
    )


async def _run(monkeypatch, a, b, off_h, call=None, water=None):
    """``call``: the call source (None: not read); ``water``: the water data source."""
    boiler = SharedBoiler(others_off_h=off_h)
    spec = ZoneSpec(
        a=a,
        b=b,
        valve=True,
        sensor_resolution_c=0.01,
        boiler=boiler,
        emitter="radiator",
        water_data="entities" if water == "entities" else None,
        options={CONF_SMART_PI_HEATING_TYPE: "radiator"},
    )
    sim = Simulation(monkeypatch, spec, Weather(base_c=3.0, daily_amplitude_c=2.0))
    boiler.actuator = sim.actuator
    options = sim.thermostat.smartpi_options
    if call == "vtherm":
        _vtherm_central_boiler(monkeypatch, sim, boiler)
        options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "vtherm_central_boiler"
    elif call == "entities":
        _called_entity(sim, boiler)
        options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "entities"
    elif call == "provider":
        sim.thermostat.hass.data.setdefault(PROVIDERS_KEY, {})["plant"] = _Provider(sim, boiler, water == "provider")
        options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "provider:plant"
        if water == "provider":
            options[CONF_SMART_PI_WATER_SOURCE] = "provider:plant"
    else:
        options[CONF_SMART_PI_BOILER_CALL_SOURCE] = "none"
    held = {"calculations": 0, "a_learned": 0, "reasons": set()}
    last_a = [sim.algo.est.learn_ok_count_a]

    def watch(sim_):
        count = sim_.algo.est.learn_ok_count_a
        if sim_.algo.boiler_mode is not None:
            held["calculations"] += sim_.algo.boiler_mode == MODE_HELD
            held["a_learned"] += count - last_a[0]
            reading = sim_.algo.boiler.reading
            if reading is not None and reading.called is False:
                held["reasons"].add(reading.reason)
        last_a[0] = count
        return False

    await sim.run_for(96.0, stop=watch)
    held["dead_time_min"] = (sim.algo.dt_est.deadtime_heat_s or 0.0) / 60.0
    return sim.algo.est.a / a, sim.algo.est.b / b, held


async def test_told_that_the_boiler_is_not_called_smartpi_keeps_its_model(monkeypatch):
    """VT's central boiler, no water data: a night without the boiler under a calling zone."""
    with monkeypatch.context() as patched:
        a_blind, _b, _held = await _run(patched, 0.04, 0.0012, (2.0, 8.0))
    a_told, b_told, held = await _run(monkeypatch, 0.04, 0.0012, (2.0, 8.0), call="vtherm")

    # Not told, SmartPI took its open valve for heat and lost its emitter's power.
    assert a_blind < 0.5
    assert 0.85 < a_told < 1.2
    assert 0.95 < b_told < 1.05
    # Nothing that needs heat was learned under the boiler that is not called, and the
    # dead time stays the valve's (10 min and the sensor): the boiler's start is not timed.
    assert held["calculations"] > 0
    assert held["a_learned"] == 0
    assert 9.0 < held["dead_time_min"] < 13.0
    # VT's live entities say why: the zone's power stays under the threshold. The plant
    # switches its boiler at its next step: in between the criteria are met while the
    # boiler is still off, which VT's rule reads as its activation delay.
    assert "below_threshold" in held["reasons"]
    assert held["reasons"] <= {"below_threshold", "activation_delay"}


async def test_the_boiler_s_call_from_an_entity(monkeypatch):
    """The same night, the call read from a binary sensor: the same model."""
    a_told, b_told, held = await _run(monkeypatch, 0.04, 0.0012, (2.0, 8.0), call="entities")

    assert 0.85 < a_told < 1.2
    assert 0.95 < b_told < 1.05
    assert held["calculations"] > 0
    assert held["a_learned"] == 0
    assert 9.0 < held["dead_time_min"] < 13.0
    assert held["reasons"] == {"not_called"}


@pytest.mark.parametrize("water", ["provider", "entities"])
async def test_a_provider_s_call_with_water_data(monkeypatch, water):
    """A provider's call over water data (from the provider, or from entities): the zone
    calls with cold water too (its device's command, not its power, says so), and nothing
    that needs heat is learned."""
    a_told, b_told, held = await _run(monkeypatch, 0.06, 0.001, (10.0, 16.0), call="provider", water=water)

    assert 0.95 < b_told < 1.05
    assert 0.9 < a_told < 1.15
    assert held["calculations"] > 0
    assert held["a_learned"] == 0
    assert held["reasons"] == {"below_threshold"}
