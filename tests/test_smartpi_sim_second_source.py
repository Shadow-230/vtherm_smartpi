"""A zone heated by two sources (scenarios Z1-Z6, documentation/en/zone_dependencies.md, section 6).

The zone thermostat's radiator is the main source and its SmartPI regulates; a
second thermostat, a heated floor or another radiator, is the additional
source and applies the main one's commands. While SmartPI learns the floor
works in blocks, so that the zone's joint fit tells both sources apart; the
fit gives SmartPI its a and b, and the floor's heat becomes a known input.
Afterwards the allocation loop moves the floor towards the rule's split. The
baseline is today's setup, two SmartPI regulating the zone independently.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest

from custom_components.vtherm_smartpi.const import CONF_SMART_PI_HEATING_TYPE, DATA_SOURCE_LINK, DOMAIN
from custom_components.vtherm_smartpi.hvac_mode import (
    VThermHvacMode_COOL,
    VThermHvacMode_HEAT,
    VThermHvacMode_OFF,
)
from custom_components.vtherm_smartpi.smartpi.const import SOURCE_COMMAND_MAX_AGE_S, SmartPIPhase
from custom_components.vtherm_smartpi.smartpi.dependency_model import (
    GATE_SOURCE_HEAT,
    SOURCE_MODE_ALLOCATION,
    SOURCE_MODE_LEARN,
)
from custom_components.vtherm_smartpi.smartpi.source_link import SourceLink

from sim.boiler import SimBoiler
from sim.metrics import tracking
from sim.runner import Simulation, SourceSpec, ZoneSpec
from sim.weather import Weather

LOCAL_ONLY = pytest.mark.skipif(
    os.environ.get("SMARTPI_SIM_LOCAL") != "1",
    reason="long simulation, run locally with SMARTPI_SIM_LOCAL=1",
)
OUTDOOR = Weather(base_c=3.0, daily_amplitude_c=3.0)
A_F = 0.03


def _split(rule: str = "lead_band", source_emitter: str = "floor_water", **parameters) -> dict:
    return {"split_rule": rule, "lead_emitter": "radiator", "source_emitter": source_emitter, **parameters}


FLOOR = SourceSpec(a_f=A_F, deadtime_min=30.0, tau_min=180.0, split=_split(lead_band_percent=20))


def _source(sim: Simulation) -> dict:
    return sim.algo.dependency_diagnostics()["second_source"]


def _link(sim: Simulation):
    return sim.thermostat.hass.data[DOMAIN][DATA_SOURCE_LINK]


async def test_z1_floor_with_a_radiator_lead_band(monkeypatch) -> None:
    """Z1/Z3: the floor works in blocks while SmartPI learns; the zone's fit gives a, b and a_f.

    Ends with Z6: the main source switched off gives its additional source
    nothing, and the floor's heat fades in the model.
    """
    sim = Simulation(monkeypatch, ZoneSpec(source=FLOOR, sensor_resolution_c=0.1), OUTDOOR)
    modes, gates, executive, published = set(), set(), [], []

    def watch(s: Simulation) -> bool:
        diagnostics = s.algo.dependency_diagnostics()
        source = diagnostics["second_source"]
        modes.add(source["mode"])
        gates.add(diagnostics["learning_gate"])
        executive.append(s.source_handler.is_executive_source)
        if source["zone_fit"]["published"] and not published:
            published.append(s.clock.elapsed_s / 3600.0)
        return False

    await sim.run_for(40.0, stop=watch)
    source = _source(sim)
    fit = source["zone_fit"]
    print(f"\nZ1: fit at {published}, {fit}, phase {sim.algo.phase}, source {source}")

    assert {SOURCE_MODE_LEARN, SOURCE_MODE_ALLOCATION} <= modes
    assert GATE_SOURCE_HEAT in gates
    assert all(executive[5:])
    assert published and published[0] < 30.0
    assert fit["a"] == pytest.approx(sim.zone.a, rel=0.2)
    assert fit["b"] == pytest.approx(sim.zone.b, rel=0.2)
    assert fit["a_f"] == pytest.approx(A_F, rel=0.2)
    # SmartPI starts from the fit and keeps its a and b.
    assert sim.algo.phase != SmartPIPhase.HYSTERESIS
    assert sim.algo.est.a == pytest.approx(fit["a"], rel=1e-3)
    assert sim.algo.est.b == pytest.approx(fit["b"], rel=1e-3)
    # The allocation loop is still on its way to the rule's split.
    after_fit = tracking(sim, 30.0, 40.0)
    assert after_fit.source_share > 0.2
    assert after_fit.mean_error_c < 0.08

    x_f_before = source["x_f"]
    await sim.set_hvac_mode(VThermHvacMode_OFF)
    await sim.run_for(2.0)
    assert sim.source_actuator.power == 0.0
    assert _source(sim)["command"] == 0.0
    assert _source(sim)["x_f"] < x_f_before


async def test_the_source_pauses_its_own_regulation_and_learning(monkeypatch) -> None:
    """Working as a source, the thermostat's SmartPI applies the lead's command and learns nothing.

    Versatile Thermostat keeps calling it on each measurement; its learned
    model stays as it was and its diagnostics show the role.
    """
    sim = Simulation(monkeypatch, ZoneSpec(source=FLOOR, sensor_resolution_c=0.01), OUTDOOR)
    await sim.run_for(0.2)
    assert sim.source_handler.is_executive_source
    before = sim.source_algo.save_state()["est_state"]
    learning = []
    monkeypatch.setattr(sim.source_algo, "update_learning", lambda *a, **k: learning.append(1))

    await sim.run_for(8.0)
    assert learning == []
    assert sim.source_algo.save_state()["est_state"] == before
    assert sim.source_algo.dependency_diagnostics() == {
        "role": "second_source",
        "lead": sim.thermostat.unique_id,
        "command": pytest.approx(sim.source_algo.on_percent, abs=1e-3),
    }

    sim.handler.remove()
    await sim.source_thermostat.async_control_heating(force=False)
    assert not sim.source_handler.is_executive_source
    await sim.run_for(0.5)
    assert learning


async def test_a_silent_lead_hands_the_zone_to_its_source(monkeypatch) -> None:
    """Z6: the main source stops working: its additional source regulates the zone on its own.

    Once the main one's commands come back, the additional source applies them again.
    """
    split = _split(rule="together", source_emitter="radiator", split_ratio=100)
    sim = Simulation(
        monkeypatch,
        ZoneSpec(source=SourceSpec(a_f=A_F, deadtime_min=10.0, tau_min=0.0, split=split), sensor_resolution_c=0.1),
        OUTDOOR,
    )
    await sim.run_for(4.0)
    assert sim.source_handler.is_executive_source

    # The lead's integration stops: no calculation, no command, its radiator off.
    control = sim.thermostat.async_control_heating

    async def dead(timestamp=None, force=False) -> bool:
        return True

    sim.thermostat.async_control_heating = dead
    await sim.scheduler.cancel_cycle()
    await sim.actuator.turn_off()
    start_h = sim.clock.elapsed_s / 3600.0
    await sim.run_for(SOURCE_COMMAND_MAX_AGE_S / 3600.0 + 0.5)
    assert not sim.source_handler.is_executive_source
    assert not _link(sim).is_active(sim.source_thermostat.unique_id, sim.clock.monotonic(), SOURCE_COMMAND_MAX_AGE_S)
    await sim.run_for(12.0)
    zone = [p.zone_c for p in sim.trace if p.t_h >= start_h + 2.0]
    print(f"\nZ6 silent lead: zone {min(zone):.2f}..{max(zone):.2f}")
    assert min(zone) > 19.3
    assert sim.source_actuator.history.on_seconds(sim.clock.elapsed_s - 6 * 3600.0, sim.clock.elapsed_s) > 0.0

    sim.thermostat.async_control_heating = control
    await sim.thermostat.async_control_heating(force=True)
    await sim.source_thermostat.async_control_heating(force=True)
    assert sim.source_handler.is_executive_source


async def test_a_lead_switched_off_with_a_long_cycle_does_not_hand_over_the_zone(monkeypatch) -> None:
    """Switched off, VTherm calls the lead only every cycle_min: it still tells its source, which stays off."""
    split = _split(rule="together", source_emitter="radiator", split_ratio=100)
    sim = Simulation(
        monkeypatch,
        ZoneSpec(
            cycle_min=20.0,
            sensor_resolution_c=0.5,
            source=SourceSpec(a_f=A_F, deadtime_min=10.0, tau_min=0.0, split=split),
        ),
        OUTDOOR,
    )
    await sim.run_for(2.0)
    await sim.set_hvac_mode(VThermHvacMode_OFF)
    roles = []

    def watch(s: Simulation) -> bool:
        roles.append(s.source_handler.is_executive_source)
        return False

    await sim.run_for(4.0, stop=watch)
    end_s = sim.clock.elapsed_s
    assert all(roles)
    assert sim.source_actuator.history.on_seconds(end_s - 3 * 3600.0, end_s) == 0.0


async def test_an_executive_source_applies_the_lead_s_power_with_its_own_water(monkeypatch) -> None:
    """The lead's command is power at the reference water: the floor turns it into its own
    command with its water factor, and tells the lead the most it can give."""
    source = SourceSpec(
        a_f=A_F,
        deadtime_min=30.0,
        tau_min=180.0,
        split=FLOOR.split,
        boiler=SimBoiler(curve_slope=0.6, max_water_c=45.0),
        emitter="floor_water",
        options={CONF_SMART_PI_HEATING_TYPE: "floor_water"},
    )
    sim = Simulation(monkeypatch, ZoneSpec(source=source, water_data="provider", initial_c=20.0), OUTDOOR)
    await sim.run_for(3.0)
    algo = sim.source_algo
    factor = algo.water_factor

    assert sim.source_handler.is_executive_source
    assert 0.2 < factor < 0.7
    assert _link(sim).power_cap(sim.source_thermostat.unique_id) == pytest.approx(factor)
    assert _source(sim)["power_cap"] == pytest.approx(factor, abs=1e-3)
    command = algo.dependency_diagnostics()["command"]
    assert algo.on_percent == pytest.approx(min(1.0, command / factor), abs=0.01)


async def test_power_shedding_stops_the_source_but_not_the_lead_s_command(monkeypatch) -> None:
    """Each thermostat sheds its own load: a shed lead still commands its source, a shed source applies 0."""
    split = _split(rule="together", source_emitter="radiator", split_ratio=100)
    sim = Simulation(
        monkeypatch,
        ZoneSpec(source=SourceSpec(a_f=A_F, deadtime_min=10.0, tau_min=0.0, split=split), initial_c=17.0),
        OUTDOOR,
    )
    await sim.run_for(1.0)
    link = _link(sim)
    source_id = sim.source_thermostat.unique_id
    monkeypatch.setattr(type(sim.algo), "source_command", property(lambda _: 0.6))

    sim.thermostat.is_overpowering_detected = True
    await sim.thermostat.async_control_heating(force=True)
    now_s = sim.clock.monotonic()
    assert link.command_for(source_id, now_s, SOURCE_COMMAND_MAX_AGE_S, str(VThermHvacMode_HEAT)) == 0.6
    await sim.source_thermostat.async_control_heating(force=True)
    assert sim.source_actuator.power > 0.0

    sim.thermostat.is_overpowering_detected = False
    sim.source_thermostat.is_overpowering_detected = True
    await sim.source_thermostat.async_control_heating(force=True)
    assert sim.source_actuator.power == 0.0


async def _restart(sim: Simulation, monkeypatch) -> None:
    """Restart Home Assistant: new algorithms and link, each loaded from its saved state."""
    lead_state = json.loads(json.dumps(sim.algo.save_state()))
    source_state = json.loads(json.dumps(sim.source_algo.save_state()))
    sim.thermostat.hass.data[DOMAIN][DATA_SOURCE_LINK] = SourceLink()
    for handler, state in ((sim.source_handler, source_state), (sim.handler, lead_state)):
        handler.init_algorithm()
        handler._store = None
        handler._thermostat.prop_algorithm.load_state(state)
    sim._drive_source(monkeypatch, sim.zone.source.split)
    for thermostat in (sim.thermostat, sim.source_thermostat):
        await thermostat.async_control_heating(force=True)


async def test_a_restart_keeps_the_zone_s_fit(monkeypatch) -> None:
    """After a restart the zone resumes with its fit: no new learning, the same a, b and a_f."""
    sim = Simulation(monkeypatch, ZoneSpec(source=FLOOR, sensor_resolution_c=0.1), OUTDOOR)
    await sim.run_for(36.0)
    fit = _source(sim)["zone_fit"]
    assert fit["published"]

    await _restart(sim, monkeypatch)
    await sim.run_for(0.5)
    after = _source(sim)
    assert after["zone_fit"]["published"]
    assert after["zone_fit"]["a_f"] == fit["a_f"]
    assert after["mode"] == SOURCE_MODE_ALLOCATION
    assert sim.algo.phase != SmartPIPhase.HYSTERESIS
    assert sim.algo.est.a == pytest.approx(fit["a"], rel=1e-3)
    start_h = sim.clock.elapsed_s / 3600.0
    await sim.run_for(12.0)
    assert tracking(sim, start_h + 2.0, start_h + 12.0).mean_error_c < 0.15


async def _setpoint(sim: Simulation, value: float) -> None:
    for thermostat in (sim.thermostat, sim.source_thermostat):
        thermostat.target_temperature = value
    sim.zone.setpoint_c = value
    for thermostat in (sim.thermostat, sim.source_thermostat):
        await thermostat.async_control_heating(force=True)


async def _night_and_morning(monkeypatch, recovery: str) -> tuple[float | None, float, bool]:
    """Thirty hours to learn, a night at 17 degC for 8 h, then 20 degC: the hours to reach
    19.8 degC, the overshoot after it and whether the night was seen as a setback."""
    split = {**FLOOR.split, "recovery": recovery}
    with monkeypatch.context() as patch:
        sim = Simulation(patch, ZoneSpec(source=replace(FLOOR, split=split), sensor_resolution_c=0.1), OUTDOOR)
        await sim.run_for(30.0)
        await _setpoint(sim, 17.0)
        await sim.run_for(8.0)
        setback = _source(sim)["setback"]
        await _setpoint(sim, 20.0)
        start_h = sim.clock.elapsed_s / 3600.0
        await sim.run_for(8.0)
    after = [p for p in sim.trace if p.t_h >= start_h]
    reach = next((p.t_h - start_h for p in after if p.zone_c >= 19.8), None)
    return reach, max(p.zone_c for p in after) - 20.0, setback


async def test_a_fast_recovery_brings_the_zone_back_sooner_than_an_economical_one(monkeypatch) -> None:
    """During the night the floor carries the zone's load: warm in the morning, without overshoot."""
    fast_reach, fast_over, fast_setback = await _night_and_morning(monkeypatch, "fast")
    eco_reach, eco_over, eco_setback = await _night_and_morning(monkeypatch, "economy")
    print(f"\nrecovery: fast {fast_reach} h {fast_over:+.2f}, economy {eco_reach} h {eco_over:+.2f}")

    assert fast_setback and eco_setback
    assert fast_reach is not None and eco_reach is not None
    assert fast_reach <= eco_reach - 0.5
    assert fast_over <= max(eco_over, 0.0) + 0.15


async def test_z7_cooling_learns_its_own_fit(monkeypatch) -> None:
    """Cooling with two sources: the zone's cooling fit is learned apart from the heating one."""
    split = _split(rule="together", source_emitter="radiator", split_ratio=100)
    sim = Simulation(
        monkeypatch,
        ZoneSpec(
            a=-0.03,
            deadtime_min=5.0,
            setpoint_c=25.0,
            initial_c=27.0,
            source=SourceSpec(a_f=-0.03, deadtime_min=5.0, tau_min=20.0, setpoint_c=25.0, split=split),
        ),
        Weather(base_c=31.0, daily_amplitude_c=4.0),
    )
    for thermostat in (sim.thermostat, sim.source_thermostat):
        thermostat.vtherm_hvac_mode = VThermHvacMode_COOL
    await sim.run_for(48.0)
    model = sim.algo.dependency_model
    cool = model.zone_fit(VThermHvacMode_COOL)
    print(f"\nZ7 cooling: fit {cool}, phase {sim.algo.phase}, error {tracking(sim, 36.0, 48.0)}")

    assert model.zone_fit(VThermHvacMode_HEAT) is None
    assert cool is not None
    assert cool.a == pytest.approx(0.03, rel=0.25)
    assert cool.a_f == pytest.approx(0.03, rel=0.25)
    assert sim.algo.est.a == pytest.approx(-cool.a, rel=1e-3)
    assert tracking(sim, 36.0, 48.0).mean_error_c < 0.15


@LOCAL_ONLY
@pytest.mark.parametrize("sensor_c", [0.01, 0.1])
async def test_z1_split_holds_the_zone_like_two_independent_smartpi(monkeypatch, sensor_c) -> None:
    """Constant setpoint, daily outdoor swing: the split holds the zone at least as well as today."""
    results = {}
    for name, split in (("split", FLOOR.split), ("independent", None)):
        with monkeypatch.context() as patch:
            source = SourceSpec(a_f=A_F, deadtime_min=30.0, tau_min=180.0, split=split)
            sim = Simulation(patch, ZoneSpec(source=source, sensor_resolution_c=sensor_c), OUTDOOR)
            await sim.run_for(96.0)
            results[name] = tracking(sim, 48.0, 96.0)
    print(f"\nZ1 {sensor_c} degC: {results}")

    assert results["split"].mean_error_c <= results["independent"].mean_error_c + 0.03
    assert results["split"].source_share > 0.35


async def _setback(sim: Simulation, value: float) -> None:
    for thermostat in (sim.thermostat, sim.source_thermostat):
        thermostat.target_temperature = value
    sim.zone.setpoint_c = value
    await sim.thermostat.async_control_heating(force=True)
    await sim.source_thermostat.async_control_heating(force=True)


@LOCAL_ONLY
async def test_z1_night_setback_does_not_run_the_floor_away(monkeypatch) -> None:
    """Night setback 20 -> 17 degC: the floor does not creep up from day to day."""
    results = {}
    for name, split in (("split", FLOOR.split), ("independent", None)):
        with monkeypatch.context() as patch:
            source = SourceSpec(a_f=A_F, deadtime_min=30.0, tau_min=180.0, split=split)
            sim = Simulation(patch, ZoneSpec(source=source, sensor_resolution_c=0.1), OUTDOOR)
            await sim.run_for(48.0)
            overshoots = []
            for _day in range(3):
                await _setback(sim, 17.0)
                await sim.run_for(8.0)
                await _setback(sim, 20.0)
                start_h = sim.clock.elapsed_s / 3600.0
                await sim.run_for(16.0)
                overshoots.append(max(p.zone_c for p in sim.trace if p.t_h >= start_h) - 20.0)
            results[name] = (overshoots, tracking(sim, 48.0, sim.clock.elapsed_s / 3600.0).source_share)
    print(f"\nZ1 setback: {results}")

    split_overshoots, split_share = results["split"]
    assert max(split_overshoots) <= max(results["independent"][0]) + 0.1
    assert split_overshoots[-1] <= max(split_overshoots[:-1]) + 0.15
    assert split_share < 0.9


@LOCAL_ONLY
@pytest.mark.parametrize(
    ("rule", "parameters"),
    [("source_first", {"source_alone_percent": 50}), ("together", {"split_ratio": 200})],
    ids=["Z2_source_first", "together_ratio_200"],
)
async def test_rules_settle_on_their_curves(monkeypatch, rule, parameters) -> None:
    """The lead's average demand ends on the rule's curve for the source's command."""
    split = _split(rule=rule, source_emitter="floor_water", **parameters)
    sim = Simulation(
        monkeypatch,
        ZoneSpec(source=SourceSpec(a_f=A_F, deadtime_min=30.0, tau_min=180.0, split=split), sensor_resolution_c=0.01),
        OUTDOOR,
    )
    await sim.run_for(96.0)
    source = _source(sim)
    # The rule's curve at the allocation: the command also carries the answer to the zone's error.
    loop = sim.algo.dependency_model._source.loop
    target = loop.rule.lead_target(loop.command)
    print(f"\n{rule}: allocation {loop.command:.3f}, lead average {source['lead_demand_avg']}, curve {target:.3f}")

    # Where the curve keeps the lead at 0 %, the lead still answers the error around it.
    assert source["lead_demand_avg"] == pytest.approx(target, abs=0.15)


@LOCAL_ONLY
async def test_z4_two_radiators_share_the_work(monkeypatch) -> None:
    """Z4: a fast source of the same power shares the work about evenly, as stably as today."""
    split = _split(rule="together", source_emitter="radiator", split_ratio=100)
    results = {}
    for name, value in (("split", split), ("independent", None)):
        with monkeypatch.context() as patch:
            source = SourceSpec(a_f=A_F, deadtime_min=10.0, tau_min=0.0, split=value)
            sim = Simulation(patch, ZoneSpec(source=source, sensor_resolution_c=0.1), OUTDOOR)
            await sim.run_for(72.0)
            results[name] = tracking(sim, 36.0, 72.0)
    print(f"\nZ4: {results}")

    assert results["split"].source_share == pytest.approx(0.5, abs=0.15)
    assert results["split"].mean_error_c <= results["independent"].mean_error_c + 0.03


@LOCAL_ONLY
async def test_z5_a_weak_lead_is_helped_and_learns_when_the_weather_allows(monkeypatch) -> None:
    """Z5: a main source too weak for the cold: the floor holds the zone from the first day.

    While the zone is cold the floor works at full power; the fit, or after
    its timeout SmartPI alone, finishes learning and the allocation follows.
    """
    zone = ZoneSpec(a=0.015, source=FLOOR, sensor_resolution_c=0.1, initial_c=17.0)
    sim = Simulation(monkeypatch, zone, Weather(base_c=-5.0, daily_amplitude_c=2.0, step_at_h=36.0, step_c=15.0))
    modes = set()

    def watch(s: Simulation) -> bool:
        modes.add(_source(s)["mode"])
        return False

    await sim.run_for(36.0, stop=watch)
    assert SOURCE_MODE_LEARN in modes
    assert min(p.zone_c for p in sim.trace if p.t_h >= 18.0) > 18.5
    await sim.run_for(60.0, stop=watch)
    print(f"\nZ5: modes {modes}, phase {sim.algo.phase}, source {_source(sim)}")

    assert sim.algo.phase != SmartPIPhase.HYSTERESIS
    assert SOURCE_MODE_ALLOCATION in modes


async def test_a_valve_source_follows_its_lead_even_when_vtherm_recalculates_it(monkeypatch) -> None:
    """Versatile Thermostat sends a valve's own algorithm output on each measurement.

    Working as a source, that output is the lead's command: with the lead
    switched off, the valve closes and stays closed.
    """
    split = _split(rule="together", source_emitter="radiator", split_ratio=100)
    source = SourceSpec(a_f=A_F, deadtime_min=10.0, tau_min=0.0, valve=True, split=split)
    sim = Simulation(monkeypatch, ZoneSpec(source=source, initial_c=18.0), OUTDOOR)
    thermostat = sim.source_thermostat
    published = thermostat.temperature_published

    async def vtherm_valve_measurement(value_c: float) -> None:
        # As ThermostatOverValve.recalculate: the algorithm's output goes to the valve at once.
        await published(value_c)
        opening = thermostat.apply_valve_command_percent(thermostat.prop_algorithm.on_percent, force=True)
        await sim.source_scheduler._apply_valve(opening)

    monkeypatch.setattr(thermostat, "temperature_published", vtherm_valve_measurement)
    await sim.run_for(8.0)
    assert sim.source_actuator.history.on_seconds(0.0, sim.clock.elapsed_s) > 0.0

    await sim.set_hvac_mode(VThermHvacMode_OFF)
    await sim.run_for(0.5)
    end_s = sim.clock.elapsed_s
    assert sim.source_actuator.history.on_seconds(end_s - 1200.0, end_s) == 0.0
