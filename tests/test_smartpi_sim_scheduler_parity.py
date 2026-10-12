"""Local-only check that the simulation scheduler behaves like VTherm's CycleScheduler.

The Versatile Thermostat scheduler is internal to Versatile Thermostat and the CI
checks out its ``master`` branch unpinned, so this comparison runs only locally
(``SMARTPI_SIM_LOCAL=1``). It replays the same reproducible series of relay
commands on both schedulers and compares relay transitions and the values handed
to SmartPI at each cycle start and end.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timezone

import pytest

from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT

from sim.clock import EventQueue, VirtualClock
from sim.scheduler import SimActuator, SimCycleScheduler

pytestmark = pytest.mark.skipif(
    os.environ.get("SMARTPI_SIM_LOCAL") != "1",
    reason="compares with Versatile Thermostat internals, run locally with SMARTPI_SIM_LOCAL=1",
)


class _Underlying:
    """Switch underlying in the shape the VTherm scheduler drives."""

    def __init__(self, clock: VirtualClock, actuator: SimActuator) -> None:
        from custom_components.versatile_thermostat.underlyings import UnderlyingEntityType

        self.entity_type = UnderlyingEntityType.SWITCH
        self._clock = clock
        self._actuator = actuator
        self._should_be_on = False
        self._on_time_sec = 0
        self._off_time_sec = 0
        self._hvac_mode = None

    @property
    def is_device_active(self) -> bool:
        return self._actuator.is_device_active

    @property
    def last_change(self) -> datetime | None:
        if self._actuator.last_change_s is None:
            return None
        return datetime.fromtimestamp(self._clock.time() - (self._clock.elapsed_s - self._actuator.last_change_s), timezone.utc)

    async def turn_on(self) -> None:
        await self._actuator.turn_on()

    async def turn_off(self) -> None:
        await self._actuator.turn_off()


class _Thermostat:
    _on_time_sec = 0
    _off_time_sec = 0

    def incremente_energy(self) -> None:
        """Energy accounting is not compared."""


class _Bench:
    """One scheduler with its own clock, timers, relay and callback log."""

    def __init__(self, scheduler_factory, valve: bool = False) -> None:
        self.clock = VirtualClock()
        self.queue = EventQueue(self.clock)
        self.actuator = SimActuator(self.clock, valve=valve)
        self.scheduler = scheduler_factory(self)
        self.log: list[tuple] = []

        async def on_start(**kwargs) -> None:
            self.log.append(("start", round(self.clock.elapsed_s, 3), kwargs["on_time_sec"], kwargs["off_time_sec"], round(kwargs["on_percent"], 6)))

        async def on_end(**kwargs) -> None:
            self.log.append(("end", round(self.clock.elapsed_s, 3), round(kwargs["e_eff"], 6), round(kwargs["elapsed_ratio"], 6)))

        self.scheduler.register_cycle_start_callback(on_start)
        self.scheduler.register_cycle_end_callback(on_end)

    async def run_until(self, end_s: float) -> None:
        while True:
            due = self.queue.next_due()
            if due is None or due > end_s:
                self.clock.advance_to(end_s)
                return
            self.clock.advance_to(due)
            await self.queue.run_due()

    def relay_changes(self) -> list[tuple[float, float]]:
        history = self.actuator.history
        return [(round(t, 3), v) for t, v in zip(history._times[1:], history._values[1:])]


def _commands(seed: int, hours: float) -> list[tuple[float, float, bool]]:
    rng = random.Random(seed)
    t_s, commands = 0.0, []
    while t_s < hours * 3600.0:
        roll = rng.random()
        on_percent = 0.0 if roll < 0.1 else 1.0 if roll < 0.2 else rng.random()
        commands.append((t_s, on_percent, rng.random() < 0.2))
        t_s += rng.uniform(30.0, 900.0)
    return commands


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize(
    ("cycle_min", "min_on_s", "min_off_s"),
    [(10.0, 0, 0), (10.0, 120, 120), (60.0, 240, 240), (15.0, 30, 300)],
)
async def test_sim_scheduler_matches_vtherm(monkeypatch, seed, cycle_min, min_on_s, min_off_s) -> None:
    """Relay transitions and cycle callbacks are the same as with VTherm's scheduler."""
    from custom_components.versatile_thermostat import cycle_scheduler as vtherm_module

    def vtherm_factory(bench: _Bench):
        monkeypatch.setattr(vtherm_module, "time", bench.clock.time_module())
        monkeypatch.setattr(
            vtherm_module,
            "async_call_later",
            lambda _hass, delay, action: bench.queue.call_later(delay, action),
        )
        monkeypatch.setattr("homeassistant.util.dt.utcnow", bench.clock.now)
        return vtherm_module.CycleScheduler(
            None,
            _Thermostat(),
            [_Underlying(bench.clock, bench.actuator)],
            cycle_min * 60.0,
            min_on_s,
            min_off_s,
        )

    def sim_factory(bench: _Bench):
        return SimCycleScheduler(
            bench.clock, bench.queue, _Thermostat(), bench.actuator, cycle_min, min_on_s, min_off_s
        )

    vtherm, sim = _Bench(vtherm_factory), _Bench(sim_factory)
    for t_s, on_percent, force in _commands(seed, hours=12.0):
        for bench in (vtherm, sim):
            await bench.run_until(t_s)
            await bench.scheduler.start_cycle(VThermHvacMode_HEAT, on_percent, force)
    for bench in (vtherm, sim):
        await bench.run_until(13 * 3600.0)

    # VTherm measures the time since the last relay change on microsecond datetimes,
    # so at a minimal-delay boundary it may retry 0.1 s apart from the simulation.
    _assert_same_events(sim.relay_changes(), vtherm.relay_changes(), time_field=0)
    _assert_same_events(sim.log, vtherm.log, time_field=1)


def _assert_same_events(actual: list[tuple], expected: list[tuple], time_field: int) -> None:
    """Compare event lists: times within 0.25 s, other numbers within 1e-3."""
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert len(got) == len(want)
        for index, (got_field, want_field) in enumerate(zip(got, want)):
            if index == time_field:
                assert got_field == pytest.approx(want_field, abs=0.25)
            elif isinstance(want_field, float):
                assert got_field == pytest.approx(want_field, abs=1e-3)
            else:
                assert got_field == want_field


class _ValveUnderlying(_Underlying):
    """Valve underlying: applies the thermostat's retained opening, as VTherm does."""

    def __init__(self, clock: VirtualClock, actuator: SimActuator, thermostat) -> None:
        from custom_components.versatile_thermostat.underlyings import UnderlyingEntityType

        super().__init__(clock, actuator)
        self.entity_type = UnderlyingEntityType.VALVE
        self._thermostat = thermostat

    async def set_valve_open_percent(self) -> None:
        await self._actuator.set_opening(self._thermostat.valve_open_percent / 100.0)


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize(("dpercent", "period_min"), [(0, 0.0), (3, 5.0)])
async def test_sim_scheduler_matches_vtherm_for_valves(monkeypatch, seed, dpercent, period_min) -> None:
    """Valve openings, cycle callbacks and the opening seen at cycle start match VTherm."""
    from custom_components.versatile_thermostat import cycle_scheduler as vtherm_module

    from sim.runner import SimThermostat, ZoneSpec

    class _ValveThermostat(SimThermostat):
        def incremente_energy(self) -> None:
            """Energy accounting is not compared."""

    zone = ZoneSpec(valve=True, valve_dpercent=dpercent, valve_period_min=period_min)

    def make_bench(vtherm: bool) -> _Bench:
        def factory(bench: _Bench):
            bench.thermostat = _ValveThermostat(zone, bench.clock, bench.actuator)
            if not vtherm:
                return SimCycleScheduler(bench.clock, bench.queue, bench.thermostat, bench.actuator, 10.0)
            monkeypatch.setattr(vtherm_module, "time", bench.clock.time_module())
            monkeypatch.setattr(
                vtherm_module,
                "async_call_later",
                lambda _hass, delay, action: bench.queue.call_later(delay, action),
            )
            return vtherm_module.CycleScheduler(
                None,
                bench.thermostat,
                [_ValveUnderlying(bench.clock, bench.actuator, bench.thermostat)],
                600.0,
            )

        bench = _Bench(factory, valve=True)
        starts = bench.log

        async def opening_at_start(**_kwargs) -> None:
            starts.append(("opening", round(bench.clock.elapsed_s, 3), bench.thermostat.valve_open_percent))

        bench.scheduler.register_cycle_start_callback(opening_at_start)
        return bench

    vtherm, sim = make_bench(True), make_bench(False)
    for t_s, on_percent, force in _commands(seed, hours=12.0):
        for bench in (vtherm, sim):
            await bench.run_until(t_s)
            await bench.scheduler.start_cycle(VThermHvacMode_HEAT, on_percent, force)
    for bench in (vtherm, sim):
        await bench.run_until(13 * 3600.0)

    _assert_same_events(sim.relay_changes(), vtherm.relay_changes(), time_field=0)
    _assert_same_events(sim.log, vtherm.log, time_field=1)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_slope_matches_vtherm(seed) -> None:
    """The moving average and slope equal VTherm's for a random measurement series."""
    from custom_components.versatile_thermostat.ema import ExponentialMovingAverage
    from custom_components.versatile_thermostat.open_window_algorithm import (
        WindowOpenDetectionAlgorithm,
    )

    from sim.sensors import VThermSlope

    rng = random.Random(seed)
    clock = VirtualClock()
    ours = VThermSlope()
    ema = ExponentialMovingAverage("parity", 300, timezone.utc, 2, 0.5)
    window = WindowOpenDetectionAlgorithm(3.0, 0.0)
    ema_value = None
    value_c, t_s = 20.0, 0.0
    for _ in range(2000):
        t_s += rng.uniform(5.0, 2400.0)
        clock.advance_to(t_s)
        if rng.random() < 0.7:
            value_c = round(value_c + rng.choice([-0.1, 0.0, 0.1]), 1)
            ours.measure(value_c, t_s)
            ema_value = ema.calculate_ema(value_c, clock.now())
            window.add_temp_measurement(ema_value, clock.now())
        elif ema_value is not None:
            ours.check_age(t_s)
            window.check_age_last_measurement(ema_value, clock.now())
        assert ours.ema_c == pytest.approx(ema_value)
        assert ours.slope_c_per_h == pytest.approx(window.last_slope)
