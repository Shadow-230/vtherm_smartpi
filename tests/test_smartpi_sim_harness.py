"""Checks of the simulation harness itself: clock, timers, cycle scheduler, determinism."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.vtherm_smartpi import algo as algo_module
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT, VThermHvacMode_OFF
from custom_components.vtherm_smartpi.smartpi import learning_window as learning_window_module

from sim.clock import EventQueue, VirtualClock
from sim.metrics import learning_finished
from sim.runner import Simulation, ZoneSpec
from sim.scheduler import SimActuator, SimCycleScheduler
from sim.sensors import VThermSlope
from sim.weather import Weather


def test_clock_drives_the_smartpi_modules(monkeypatch) -> None:
    """SmartPI modules read the virtual wall clock, monotonic clock and datetime."""
    clock = VirtualClock()
    clock.install(monkeypatch)
    clock.advance_to(5400.0)

    assert algo_module.time.time() == clock.time()
    assert algo_module.time.monotonic() == clock.monotonic()
    assert learning_window_module.datetime.now().timestamp() == pytest.approx(clock.time())


async def test_event_queue_fires_in_due_order() -> None:
    """Timers fire by due time, ties in registration order, and can be cancelled."""
    clock = VirtualClock()
    queue = EventQueue(clock)
    fired: list[str] = []
    queue.call_later(20.0, lambda _now: fired.append("late"))
    queue.call_later(10.0, lambda _now: fired.append("first"))
    queue.call_later(10.0, lambda _now: fired.append("second"))
    cancel = queue.call_later(15.0, lambda _now: fired.append("cancelled"))
    cancel()

    clock.advance_to(20.0)
    await queue.run_due()

    assert fired == ["first", "second", "late"]


def _scheduler(cycle_min: float = 10.0, valve: bool = False, **delays):
    clock = VirtualClock()
    queue = EventQueue(clock)
    actuator = SimActuator(clock, valve=valve)
    scheduler = SimCycleScheduler(clock, queue, MagicMock(), actuator, cycle_min, **delays)
    starts: list[dict] = []
    ends: list[dict] = []

    async def on_start(**kwargs) -> None:
        starts.append(kwargs)

    async def on_end(**kwargs) -> None:
        ends.append(kwargs)

    scheduler.register_cycle_start_callback(on_start)
    scheduler.register_cycle_end_callback(on_end)
    return clock, queue, actuator, scheduler, starts, ends


async def _run_until(clock: VirtualClock, queue: EventQueue, end_s: float) -> None:
    while True:
        due = queue.next_due()
        if due is None or due > end_s:
            clock.advance_to(end_s)
            return
        clock.advance_to(due)
        await queue.run_due()


async def test_scheduler_runs_pwm_cycles() -> None:
    """A 40 % command keeps the relay on 240 s of each 600 s cycle and reports it."""
    clock, queue, actuator, scheduler, starts, ends = _scheduler()

    await scheduler.start_cycle(VThermHvacMode_HEAT, 0.4)
    await _run_until(clock, queue, 1200.5)

    assert starts[0] == {
        "on_time_sec": 240,
        "off_time_sec": 360,
        "on_percent": 0.4,
        "hvac_mode": VThermHvacMode_HEAT,
    }
    assert actuator.history.on_seconds(0.0, 1200.0) == pytest.approx(480.0)
    assert ends[0]["e_eff"] == pytest.approx(0.4)
    assert ends[0]["elapsed_ratio"] == pytest.approx(1.0)
    assert len(starts) == 3


async def test_scheduler_applies_minimal_delays_to_the_command() -> None:
    """Too short on or off periods collapse to 0 % or 100 %, as in VTherm."""
    clock, queue, actuator, scheduler, starts, _ends = _scheduler(
        min_activation_delay=120, min_deactivation_delay=120
    )

    await scheduler.start_cycle(VThermHvacMode_HEAT, 0.1)
    assert starts[-1]["on_time_sec"] == 0
    assert not actuator.is_device_active

    await scheduler.start_cycle(VThermHvacMode_HEAT, 0.9, force=True)
    assert starts[-1]["on_time_sec"] == 600
    assert actuator.is_device_active


async def test_scheduler_delays_a_turn_on_that_breaks_the_minimal_off_time() -> None:
    """A relay switched off moments ago stays off until its minimal off time has passed."""
    clock, queue, actuator, scheduler, _starts, ends = _scheduler(min_deactivation_delay=120)
    await actuator.turn_on()
    clock.advance_to(600.0)
    await actuator.turn_off()
    clock.advance_to(630.0)

    await scheduler.start_cycle(VThermHvacMode_HEAT, 0.5)
    assert not actuator.is_device_active

    await _run_until(clock, queue, 720.0)
    assert actuator.is_device_active
    await _run_until(clock, queue, 1230.5)
    assert actuator.history.on_seconds(630.0, 1230.0) == pytest.approx(210.0)
    assert ends[0]["e_eff"] < 0.5


async def test_scheduler_valve_follows_updates_within_the_cycle() -> None:
    """A valve applies every update at once and reports the time-weighted opening."""
    clock, queue, actuator, scheduler, starts, ends = _scheduler(valve=True)

    await scheduler.start_cycle(VThermHvacMode_HEAT, 0.2)
    clock.advance_to(300.0)
    await scheduler.start_cycle(VThermHvacMode_HEAT, 0.6)
    assert actuator.power == pytest.approx(0.6)
    await _run_until(clock, queue, 600.5)

    assert len(starts) == 2
    assert ends[0]["e_eff"] == pytest.approx(0.4)


async def test_simulation_is_deterministic(monkeypatch) -> None:
    """Two runs of the same scenario give identical traces."""
    traces = []
    for _ in range(2):
        with monkeypatch.context() as patch:
            sim = Simulation(patch, ZoneSpec(), Weather(base_c=5.0, daily_amplitude_c=3.0))
            await sim.run_for(6.0)
            traces.append(sim.trace)

    assert traces[0] == traces[1]


def test_slope_keeps_an_unrounded_moving_average() -> None:
    """Small steps accumulate: VTherm rounds only the published average, not its state."""
    slope = VThermSlope()
    slope.measure(20.0, 0.0)
    slope.measure(20.1, 300.0)
    assert slope.ema_c == pytest.approx(20.05)

    for step in range(1, 61):
        slope.measure(20.1, 300.0 + 10.0 * step)

    assert slope.ema_c > 20.08


async def test_vtherm_runs_its_control_cycle_every_cycle_min(monkeypatch) -> None:
    """Besides sensor updates and the 60 s timer, VTherm calls control every cycle_min."""
    sim = Simulation(monkeypatch, ZoneSpec(cycle_min=10.0), Weather(base_c=5.0))
    periodic: list[float] = []
    control_heating = sim.handler.control_heating

    async def counting(timestamp=None, force=False):
        if timestamp is not None:
            periodic.append(sim.clock.elapsed_s)
        await control_heating(timestamp, force)

    monkeypatch.setattr(sim.handler, "control_heating", counting)
    await sim.run_for(1.0)

    assert periodic == [600.0, 1200.0, 1800.0, 2400.0, 3000.0, 3600.0]


@pytest.mark.parametrize(
    "zone",
    [
        ZoneSpec(valve=True),
        ZoneSpec(valve=True, valve_dpercent=3, valve_period_min=5.0),
    ],
    ids=["passthrough", "retained"],
)
async def test_valve_cycle_starts_report_the_applied_opening(monkeypatch, zone) -> None:
    """SmartPI sees, at every cycle start, the opening the valve actually applies."""
    sim = Simulation(monkeypatch, zone, Weather(base_c=5.0))
    starts: list[tuple[float, float | None, float]] = []
    on_cycle_started = sim.algo.on_cycle_started

    async def recording(on_time_sec, off_time_sec, on_percent, hvac_mode, physical_on_percent=None):
        starts.append((on_percent, physical_on_percent, sim.actuator.power))
        await on_cycle_started(on_time_sec, off_time_sec, on_percent, hvac_mode, physical_on_percent)

    monkeypatch.setattr(sim.algo, "on_cycle_started", recording)
    # Until its learning phase ends SmartPI drives the valve fully open or closed; with
    # learning slopes from the raw reading that phase lasts about 15 h here.
    await sim.run_for(24.0, stop=learning_finished)
    await sim.run_for(2.0)

    assert len(starts) > 40
    assert any(0.0 < command < 1.0 for command, _physical, _power in starts)
    for command, physical, _power in starts:
        assert physical == pytest.approx(command)
        assert command * 100.0 == pytest.approx(round(command * 100.0))


async def test_off_mode_switches_the_heater_off(monkeypatch) -> None:
    """Switching the thermostat to OFF stops the cycle and the heater until HEAT returns."""
    sim = Simulation(monkeypatch, ZoneSpec(initial_c=17.0), Weather(base_c=5.0))
    await sim.run_for(0.5)
    assert sim.actuator.is_device_active

    await sim.set_hvac_mode(VThermHvacMode_OFF)
    off_at_s = sim.clock.elapsed_s
    assert not sim.actuator.is_device_active
    await sim.run_for(2.0)
    assert sim.actuator.history.on_seconds(off_at_s, sim.clock.elapsed_s) == 0.0

    await sim.set_hvac_mode(VThermHvacMode_HEAT)
    await sim.run_for(0.5)
    assert sim.actuator.is_device_active
