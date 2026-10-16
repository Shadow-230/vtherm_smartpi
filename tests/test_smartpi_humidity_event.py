"""Humidity events: a shower or cooking seen in the room's humidity, and what SmartPI does then."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

import custom_components.vtherm_smartpi.algo as algo_module
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_COOL, VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import (
    HEATING_RADIATOR,
    HUMIDITY_EVENT_MAX_S,
    HUMIDITY_EVENT_SETTLE_S,
    HUMIDITY_EVENT_WINDOW_S,
    SmartPIPhase,
)
from custom_components.vtherm_smartpi.smartpi.diagnostics import build_published_diagnostics
from custom_components.vtherm_smartpi.smartpi.ff_trim import CausalFFTrimResult
from custom_components.vtherm_smartpi.smartpi.humidity_event import (
    GATE_HUMIDITY_EVENT,
    STATE_EVENT,
    STATE_IDLE,
    STATE_SETTLING,
    HumidityEvent,
)
from tests.helpers import force_smartpi_stable_mode

MIN = 60.0


def _feed(event: HumidityEvent, levels, start_s=0.0, step_s=MIN) -> float:
    """One reading per step; returns the time of the last one."""
    now = start_s
    for index, level in enumerate(levels):
        now = start_s + index * step_s
        event.update(now, level)
    return now


# The detector


def test_a_shower_s_rise_starts_an_event() -> None:
    event = HumidityEvent()
    _feed(event, [55.0, 55.5, 57.0, 59.0, 60.0])
    assert event.state == STATE_EVENT and event.active
    assert (event.baseline, event.peak, event.count) == (55.0, 60.0, 1)


def test_a_slow_rise_is_no_event() -> None:
    """Weather or a closed window: 5 %RH over more than the window."""
    event = HumidityEvent()
    step = HUMIDITY_EVENT_WINDOW_S / 4 + 1.0
    _feed(event, [55.0, 56.3, 57.6, 58.9, 60.2, 61.5], step_s=step)
    assert not event.active and event.count == 0


def test_the_event_ends_at_a_quarter_of_its_rise_then_settles() -> None:
    event = HumidityEvent()
    now = _feed(event, [55.0, 65.0, 80.0, 75.0, 62.0])
    assert event.state == STATE_EVENT and event.peak == 80.0
    # 55 + 0.25 * 25 = 61.25
    event.update(now + MIN, 61.0)
    assert event.state == STATE_SETTLING and event.active
    event.update(now + MIN + HUMIDITY_EVENT_SETTLE_S - 1.0, 60.0)
    assert event.active
    event.update(now + MIN + HUMIDITY_EVENT_SETTLE_S, 60.0)
    assert event.state == STATE_IDLE and not event.active


def test_a_humidity_that_stays_high_ends_the_event_after_its_longest() -> None:
    event = HumidityEvent()
    _feed(event, [55.0, 70.0])
    event.update(MIN + HUMIDITY_EVENT_MAX_S - 1.0, 70.0)
    assert event.state == STATE_EVENT
    event.update(MIN + HUMIDITY_EVENT_MAX_S, 70.0)
    assert event.state == STATE_SETTLING


def test_the_settled_room_s_level_is_no_new_rise() -> None:
    """After the settling the event's high tail is the new history, not a rise from before."""
    event = HumidityEvent()
    _feed(event, [55.0, 70.0])
    event.update(MIN + HUMIDITY_EVENT_MAX_S, 70.0)
    settled = MIN + HUMIDITY_EVENT_MAX_S + HUMIDITY_EVENT_SETTLE_S
    _feed(event, [70.0, 70.5, 71.0], start_s=settled)
    assert not event.active and event.count == 1


@pytest.mark.parametrize("reading", [None, float("nan"), float("inf"), "65", True])
def test_without_a_usable_reading_nothing_starts(reading) -> None:
    event = HumidityEvent()
    event.update(0.0, 55.0)
    event.update(MIN, reading)
    assert not event.active and event.humidity is None


def test_an_event_ends_by_its_own_clock_without_readings() -> None:
    event = HumidityEvent()
    _feed(event, [55.0, 70.0])
    event.update(MIN + HUMIDITY_EVENT_MAX_S, None)
    assert event.state == STATE_SETTLING
    event.update(MIN + HUMIDITY_EVENT_MAX_S + HUMIDITY_EVENT_SETTLE_S, None)
    assert event.state == STATE_IDLE


# SmartPI during an event


class _Clock:
    def __init__(self, monkeypatch):
        self.now = 1000.0
        monkeypatch.setattr(algo_module.time, "monotonic", lambda: self.now)

    def advance(self, seconds):
        self.now += seconds


def _algo() -> SmartPI:
    algo = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="TestHumidity"
    )
    algo.configure_heating_type(HEATING_RADIATOR)
    force_smartpi_stable_mode(algo)
    return algo


def _run(algo, temp=19.5, hvac=VThermHvacMode_HEAT, target=20.0):
    algo.calculate(target, temp, 5.0, 0.0, hvac)


def _shower(monkeypatch, hvac=VThermHvacMode_HEAT, target=20.0) -> tuple[_Clock, SmartPI]:
    clock = _Clock(monkeypatch)
    algo = _algo()
    algo.update_humidity(55.0)
    _run(algo, hvac=hvac, target=target)
    clock.advance(MIN)
    algo.update_humidity(75.0)
    _run(algo, hvac=hvac, target=target)
    assert algo.humidity_event.active
    return clock, algo


def test_without_a_humidity_sensor_nothing_changes(monkeypatch):
    _Clock(monkeypatch)
    algo = _algo()
    algo.update_humidity(None)
    _run(algo)
    assert algo.learning_gate is None
    assert "humidity" not in build_published_diagnostics(algo)


def test_learning_waits_during_an_event_and_its_settling(monkeypatch):
    clock, algo = _shower(monkeypatch)
    assert algo.learning_gate == GATE_HUMIDITY_EVENT
    learned = []
    monkeypatch.setattr(algo, "update_learning", lambda **kwargs: learned.append(kwargs))
    clock.advance(MIN)
    _run(algo)
    assert learned == []

    algo.update_humidity(55.0)
    clock.advance(MIN)
    _run(algo)
    assert algo.humidity_event.state == STATE_SETTLING and algo.learning_gate == GATE_HUMIDITY_EVENT
    clock.advance(HUMIDITY_EVENT_SETTLE_S)
    _run(algo)
    assert algo.learning_gate is None


def test_the_zone_s_dependencies_do_not_learn_during_an_event(monkeypatch):
    clock, algo = _shower(monkeypatch)
    later = clock.now + 24 * 3600.0  # past the restart's learning pause
    assert not algo._dependency_learning_allowed(later, VThermHvacMode_HEAT, None, False)
    monkeypatch.setattr(algo.humidity_event, "state", STATE_IDLE)
    assert algo._dependency_learning_allowed(later, VThermHvacMode_HEAT, None, False)


@pytest.mark.parametrize(
    "hvac, target, integral, room",
    [(VThermHvacMode_HEAT, 20.0, 10.0, 20.15), (VThermHvacMode_COOL, 24.0, 10.0, 24.15)],
    ids=["heat", "cool"],
)
def test_the_integral_is_held_during_an_event(monkeypatch, hvac, target, integral, room):
    """The event's heat would unwind the integral's held need (in heating) or add to it (in
    cooling); without an event the same error moves it."""
    # Cooling resets the heating model's half: keep SmartPI regulating.
    monkeypatch.setattr(SmartPI, "phase", property(lambda self: SmartPIPhase.STABLE))

    def moved(with_event: bool) -> float:
        clock, algo = _shower(monkeypatch, hvac=hvac, target=target)
        if not with_event:
            algo.update_humidity(None)
            algo.humidity_event.state = STATE_IDLE
        # No hold of the governance's own (the cycle's regimes change in this short run).
        gains_and_ff = algo._apply_gains_and_ff
        monkeypatch.setattr(algo, "_apply_gains_and_ff", lambda *a, **kw: (gains_and_ff(*a, **kw)[0], False))
        algo.ctl.integral = integral
        clock.advance(MIN)
        _run(algo, temp=room, hvac=hvac, target=target)
        if with_event:
            assert algo.ctl.last_i_mode == "I:HOLD"
            assert algo.ctl.last_integral_hold_source == GATE_HUMIDITY_EVENT
        return algo.ctl.integral - integral

    assert moved(with_event=True) == 0.0
    assert moved(with_event=False) != 0.0


def test_the_feed_forward_trim_takes_no_window_from_an_event(monkeypatch):
    _clock, algo = _shower(monkeypatch)
    result = CausalFFTrimResult(
        admissible=True,
        reason="periodic_window_ready",
        correction=-0.01,
        target_trim=-0.01,
        mean_causal_power=0.04,
        mean_ff1=0.08,
        mean_temperature=20.0,
        mean_error=0.0,
        mean_slope_h=0.0,
        observed_hold_power=0.04,
        duration_s=1800.0,
        measurement_count=7,
        alignment_delay_s=120.0,
        power_coverage_ratio=1.0,
        transfer_eligible=False,
        transfer_reason="periodic_equilibrium",
        transfer_quality="valve_segmented_linear",
        observation_mode="periodic",
    )
    assert algo._apply_fftrim_observer_result(replace(result), count_window=False) is False
    assert algo._last_fftrim_cycle_admissible is False
    assert algo._ff_trim.last_transaction is None


def test_the_diagnostics_show_the_event(monkeypatch):
    _clock, algo = _shower(monkeypatch)
    published = build_published_diagnostics(algo)["humidity"]
    assert published == {"humidity": 75.0, "state": STATE_EVENT, "baseline": 55.0, "peak": 75.0, "events": 1}
