"""The zone with two sources over a set of rooms, against two independent SmartPI.

Long simulations, run locally (``SMARTPI_SIM_LOCAL=1``): twelve rooms (six
pairs of losses and radiator power, mild and cold weather), two neighbouring
zones with a door, a floor driven through a valve, a slab warming through its
depth, and water heating on a heating curve with water data from entities. Each case
runs the zone with its two sources and, as the baseline, today's setup of two
SmartPI regulating the zone independently.

Each room: day 1 from 1 degC below the setpoint, days 3-4 at a steady setpoint,
then three nights at 17 degC (6, 8 and 10 h). The printed lines (also appended
to the file named by ``SMARTPI_SIM_REPORT``, as parallel runs do not show them)
are the record of the trial in documentation/en/zone_dependencies.md, section 6.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT

from sim.boiler import SimBoiler
from sim.metrics import tracking
from sim.plant import Schedule
from sim.runner import CoupledSimulation, Simulation, SimWorld, SimZoneNeighbour, SourceSpec, ZoneSpec
from sim.weather import Weather

pytestmark = pytest.mark.skipif(
    os.environ.get("SMARTPI_SIM_LOCAL") != "1",
    reason="long simulation, run locally with SMARTPI_SIM_LOCAL=1",
)

# (b, a): losses and radiator power; the floor gives a_f = a.
ROOMS = {
    "slowcool-slowheat": (0.0005, 0.015),
    "slowcool-fastheat": (0.0005, 0.04),
    "base": (0.001, 0.03),
    "base-slowheat": (0.001, 0.02),
    "fastcool-fastheat": (0.002, 0.06),
    "fastcool-midheat": (0.002, 0.04),
}
WEATHERS = {"mild": 3.0, "cold": -8.0}
LEAD_BAND = {"split_rule": "lead_band", "lead_band_percent": 20, "lead_emitter": "radiator", "source_emitter": "floor_water"}
NIGHTS_H = (6.0, 8.0, 10.0)
# The one case where the fast recovery stays slower than two independent SmartPI: 3.6 h against
# 2.9 h on average. The night's load there is above the floor's full power (b 0.002 x 25 degC
# against a_f 0.04), so the fast recovery cannot charge the floor more. Accepted
# (documentation/en/zone_dependencies.md, section 5.8).
RECOVERY_SLOWER_H = {"fastcool-midheat cold": 0.8}
# The control call's phase within the cycle and the 0.1 degC sensor move a morning by a few
# minutes either way: with phases of 0, 170 and 340 s the fastest room varies by 0.2 h.
RECOVERY_TOLERANCE_H = 0.2


def _report(text: str) -> None:
    print(text)
    path = os.environ.get("SMARTPI_SIM_REPORT")
    if path:
        with open(path, "a", encoding="utf-8") as report:
            report.write(text.strip("\n") + "\n")


@dataclass
class RoomResult:
    day1: float
    steady: float
    signed: float
    overshoots: list[float] = field(default_factory=list)
    reach_h: list[float | None] = field(default_factory=list)
    fit: object | None = None
    fit_h: float | None = None
    learned_h: float | None = None

    def line(self) -> str:
        nights = "/".join(f"{o:+.2f}" for o in self.overshoots)
        reach = "/".join("-" if r is None else f"{r:.1f}" for r in self.reach_h)
        return (
            f"d1={self.day1:.3f} steady={self.steady:.3f} signed={self.signed:+.3f} over={nights} reach={reach} "
            f"fit_h={self.fit_h} learned_h={self.learned_h}"
        )


async def _setpoint(sim: Simulation, value: float) -> None:
    for thermostat in (sim.thermostat, sim.source_thermostat):
        if thermostat is not None:
            thermostat.target_temperature = value
    sim.zone.setpoint_c = value
    await sim.thermostat.async_control_heating(force=True)
    if sim.source_thermostat is not None:
        await sim.source_thermostat.async_control_heating(force=True)


def _watch(result: RoomResult):
    def watch(s: Simulation) -> bool:
        t_h = s.clock.elapsed_s / 3600.0
        if result.learned_h is None and str(s.algo.phase.value) != "Hysteresis":
            result.learned_h = round(t_h, 1)
        if s.drives_source and result.fit_h is None:
            if s.algo.dependency_model.zone_fit(VThermHvacMode_HEAT) is not None:
                result.fit_h = round(t_h, 1)
        return False

    return watch


async def run_room(sim: Simulation) -> RoomResult:
    """Day 1, steady days 3-4 and three nights with a setback."""
    result = RoomResult(0.0, 0.0, 0.0)
    watch = _watch(result)
    setpoint = sim.zone.setpoint_c
    await sim.run_for(96.0, stop=watch)
    result.day1 = tracking(sim, 0.0, 24.0).mean_error_c
    result.steady = tracking(sim, 48.0, 96.0).mean_error_c
    points = [p for p in sim.trace if 48.0 <= p.t_h < 96.0]
    result.signed = sum(p.zone_c - setpoint for p in points) / len(points)
    for hours in NIGHTS_H:
        await _setpoint(sim, 17.0)
        await sim.run_for(hours, stop=watch)
        await _setpoint(sim, setpoint)
        start_h = sim.clock.elapsed_s / 3600.0
        await sim.run_for(24.0 - hours, stop=watch)
        after = [p for p in sim.trace if p.t_h >= start_h]
        result.reach_h.append(next((p.t_h - start_h for p in after if p.zone_c >= setpoint - 0.2), None))
        result.overshoots.append(max(p.zone_c for p in after) - setpoint)
    if sim.drives_source:
        result.fit = sim.algo.dependency_model.zone_fit(VThermHvacMode_HEAT)
    return result


def _fit_line(result: RoomResult, a: float, b: float, a_f: float) -> str:
    fit = result.fit
    if fit is None:
        return "fit=-"
    return f"fit=a{fit.a / a:.2f},b{fit.b / b:.2f},af{fit.a_f / a_f:.2f},delay{fit.lead_delay_s / 60:.0f}"


def _compare(name: str, split: RoomResult, independent: RoomResult, fit_line: str) -> None:
    _report(f"\nSET {name} split: {split.line()} {fit_line}\nSET {name} indep: {independent.line()}")
    assert split.fit is not None and split.fit_h is not None and split.fit_h <= 30.0
    assert split.steady <= independent.steady + 0.02
    # With the fast recovery (the default), the zone is back at its setpoint after a night setback
    # no later than with two independent SmartPI, and overshoots less.
    assert max(split.overshoots) < max(independent.overshoots)
    assert None not in split.reach_h
    split_reach = sum(split.reach_h) / len(split.reach_h)
    independent_reach = sum(r for r in independent.reach_h if r is not None) / len(independent.reach_h)
    assert split_reach <= independent_reach + RECOVERY_SLOWER_H.get(name, RECOVERY_TOLERANCE_H)


@pytest.mark.parametrize("weather", list(WEATHERS))
@pytest.mark.parametrize("room", list(ROOMS))
async def test_rooms(monkeypatch, room, weather) -> None:
    """A radiator and a floor (lead band 20 %) in twelve rooms, 0.1 degC sensors."""
    b, a = ROOMS[room]
    results = {}
    for name, split in (("split", LEAD_BAND), ("independent", None)):
        with monkeypatch.context() as patch:
            source = SourceSpec(a_f=a, deadtime_min=30.0, tau_min=180.0, split=split)
            sim = Simulation(
                patch,
                ZoneSpec(a=a, b=b, source=source, sensor_resolution_c=0.1),
                Weather(base_c=WEATHERS[weather], daily_amplitude_c=3.0),
            )
            results[name] = await run_room(sim)
    split = results["split"]
    _compare(f"{room} {weather}", split, results["independent"], _fit_line(split, a, b, a))
    assert split.fit.a == pytest.approx(a, rel=0.15)
    assert split.fit.b == pytest.approx(b, rel=0.15)
    assert split.fit.a_f == pytest.approx(a, rel=0.15)


@pytest.mark.parametrize(
    "case",
    ["valve", "two_stage", "water"],
)
async def test_installations(monkeypatch, case) -> None:
    """A floor behind a valve, a slab warming through its depth, water on a heating curve."""
    b, a = ROOMS["base"]
    results = {}
    for name, split in (("split", LEAD_BAND), ("independent", None)):
        with monkeypatch.context() as patch:
            zone = {}
            source = {"a_f": a, "deadtime_min": 30.0, "tau_min": 180.0, "split": split}
            if case == "valve":
                source["valve"] = True
            elif case == "two_stage":
                source["stages"] = 2
            else:
                # The radiator's power is given at 60 degC, the floor's at 45 degC.
                zone = {
                    "boiler": SimBoiler(curve_slope=1.2),
                    "emitter": "radiator",
                    "water_data": "entities",
                    "options": {CONF_SMART_PI_HEATING_TYPE: "radiator"},
                }
                source["boiler"] = SimBoiler(curve_slope=0.6, max_water_c=45.0)
                source["emitter"] = "floor_water"
                source["options"] = {CONF_SMART_PI_HEATING_TYPE: "floor_water"}
            sim = Simulation(
                patch,
                ZoneSpec(a=a, b=b, source=SourceSpec(**source), sensor_resolution_c=0.1, **zone),
                Weather(base_c=WEATHERS["mild"], daily_amplitude_c=3.0),
            )
            results[name] = await run_room(sim)
    split = results["split"]
    _compare(case, split, results["independent"], _fit_line(split, a, b, a))


TWO_ZONES = {
    # a, b, setpoint, a_f, floor delay, floor lag
    "A": (0.03, 0.001, 20.0, 0.03, 30.0, 180.0),
    "B": (0.025, 0.0012, 21.0, 0.02, 40.0, 240.0),
}
K_DOOR = 0.004


@pytest.mark.parametrize("door", ["always", "door"])
async def test_two_zones(monkeypatch, door) -> None:
    """Two neighbouring zones, each with a radiator and a floor, open to each other or by a door."""
    days = 6
    doors = tuple((24.0 * d + 6.0, 24.0 * d + 23.0) for d in range(days + 1)) if door == "door" else ()
    results = {}
    for name, split in (("split", LEAD_BAND), ("independent", None)):
        with monkeypatch.context() as patch:
            world = SimWorld.create(patch)
            weather = Weather(base_c=3.0, daily_amplitude_c=3.0)
            neighbours = {
                "A": SimZoneNeighbour("zone_b", K_DOOR, mode=door, doors_open_h=doors),
                "B": SimZoneNeighbour("zone_a", K_DOOR, mode=door, doors_open_h=doors),
            }
            sims = {}
            for key, (a, b, setpoint, a_f, lag, tau) in TWO_ZONES.items():
                spec = ZoneSpec(
                    name=f"Zone {key}",
                    a=a,
                    b=b,
                    setpoint_c=setpoint,
                    initial_c=setpoint - 1.0,
                    sensor_resolution_c=0.1,
                    neighbours=(neighbours[key],) if split is not None else (),
                    source=SourceSpec(a_f=a_f, deadtime_min=lag, tau_min=tau, split=split, setpoint_c=setpoint),
                )
                sims[key] = Simulation(
                    patch,
                    spec,
                    weather,
                    world=world,
                    node=key,
                    suffix=f"-{key.lower()}",
                    dependency_layer=split is not None,
                )
            neighbours["A"].zone, neighbours["B"].zone = sims["B"], sims["A"]
            schedule = Schedule(tuple((start * 3600.0, end * 3600.0) for start, end in doors)) if doors else None
            world.network.link("A", "B", K_DOOR, schedule)
            await CoupledSimulation(world, list(sims.values())).run_for(24.0 * days)
            results[name] = sims
    lines = []
    for key in TWO_ZONES:
        split, independent = (tracking(results[name][key], 72.0, 24.0 * days) for name in ("split", "independent"))
        fit = results["split"][key].algo.dependency_model.zone_fit(VThermHvacMode_HEAT)
        a, b, _setpoint, a_f, _lag, _tau = TWO_ZONES[key]
        fit_s = "-" if fit is None else f"a{fit.a / a:.2f},b{fit.b / b:.2f},af{fit.a_f / a_f:.2f},k{[round(v / K_DOOR, 2) for _, v in fit.k]}"
        lines.append(
            f"{key}: split {split.mean_error_c:.3f} (+{split.max_above_c:.2f}/-{split.max_below_c:.2f}) "
            f"indep {independent.mean_error_c:.3f} fit={fit_s}"
        )
    _report(f"\nSET two zones {door}: " + " | ".join(lines))
    for key in TWO_ZONES:
        split, independent = (tracking(results[name][key], 72.0, 24.0 * days) for name in ("split", "independent"))
        assert results["split"][key].algo.dependency_model.zone_fit(VThermHvacMode_HEAT) is not None
        assert split.mean_error_c <= independent.mean_error_c + 0.02
