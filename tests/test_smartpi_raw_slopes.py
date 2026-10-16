"""Learning slopes come from the raw measured temperature, also with the fork's inputs.

The learning windows read ``dt_est.tin_history``. It holds the raw measurement,
not the EMA-filtered ``t_int_lp`` (which only feeds the dead-time detection), so
a 0.1 degC sensor gives the true slope. The fork's additions keep working on it:
the known-input rate is removed from the raw slope, a zone held by a boiler that
is not called keeps recording raw samples, and a boiler-call edge empties both
histories.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import custom_components.vtherm_smartpi.algo as algo_module
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.boiler_call import MODE_HELD, BoilerReading
from custom_components.vtherm_smartpi.smartpi.const import (
    HEATING_RADIATOR,
    FreezeReason,
    GovernanceDecision,
)
from custom_components.vtherm_smartpi.smartpi.deadtime_estimator import DeadTimeEstimator
from custom_components.vtherm_smartpi.smartpi.learning_window import LearningWindowManager
from custom_components.vtherm_smartpi.smartpi.tint_filter import AdaptiveTintFilter
from tests.helpers import force_smartpi_stable_mode

NOT_CALLED = BoilerReading(False, "no_demand", "provider:test")
CALLED = BoilerReading(True, None, "provider:test")
# A slow passive cooling read by a 0.1 degC sensor: one step every 10 minutes.
TRUE_SLOPE_C_PER_MIN = -0.01


def _quantise(value: float) -> float:
    return round(round(value / 0.1) * 0.1, 1)


def _learned_slope(known_rate: float) -> float:
    """Run an OFF learning window over a quantised ramp and return the slope it submits."""
    dt_est = DeadTimeEstimator()
    tint_filter = AdaptiveTintFilter("test")
    window = LearningWindowManager("test")
    estimator = MagicMock(learn_skip_count=0, learn_last_reason="")
    governance = MagicMock()
    governance.decide_update.return_value = (GovernanceDecision.ADAPT_ON, FreezeReason.NONE)

    for minute in range(240):
        now = 1000.0 + 60.0 * minute
        raw = _quantise(20.03 + TRUE_SLOPE_C_PER_MIN * minute)
        t_int_lp, _ = tint_filter.update(raw, now)
        dt_est.update(now, t_int_lp, 21.0, 0.0, tin_raw=raw)
        window.update(
            dt_min=1.0,
            current_temp=raw,
            ext_temp=5.0,
            u_active=0.0,
            setpoint_changed=False,
            estimator=estimator,
            dt_est=dt_est,
            governance=governance,
            learning_resume_ts=None,
            now=now,
            in_deadband=False,
            in_near_band=False,
            t_heat_episode_start=None,
            t_cool_episode_start=None,
            hvac_mode=VThermHvacMode_HEAT,
            target_temp=21.0,
            known_rate=known_rate,
        )
        if estimator.learn.called:
            return estimator.learn.call_args.kwargs["dT_int_per_min"]
    raise AssertionError(f"no slope submitted: {estimator.learn_last_reason}")


@pytest.mark.parametrize("known_rate", [0.0, 0.004])
def test_learning_slope_uses_raw_samples_and_removes_known_rate(known_rate) -> None:
    """A slow quantised ramp gives its true slope, minus the rate the known inputs add."""
    # On the filtered history the same window measured about 0.58 of the true slope.
    assert _learned_slope(known_rate) == pytest.approx(TRUE_SLOPE_C_PER_MIN - known_rate, rel=0.05)


class _Clock:
    def __init__(self, monkeypatch):
        self.now = 1000.0
        monkeypatch.setattr(algo_module.time, "monotonic", lambda: self.now)

    def advance(self, seconds):
        self.now += seconds


def _held(monkeypatch) -> tuple[_Clock, SmartPI]:
    """A radiator zone calling under a boiler that is not called, past the emitter's settling."""
    clock = _Clock(monkeypatch)
    algo = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="RawSlopes"
    )
    algo.configure_heating_type(HEATING_RADIATOR)
    force_smartpi_stable_mode(algo)
    algo.update_boiler(NOT_CALLED)
    algo._set_committed_actuator_output(0.4)
    algo.calculate(20.0, 19.5, 5.0, 0.0, VThermHvacMode_HEAT)
    clock.advance(algo.boiler.settle_s)
    algo.calculate(20.0, 19.5, 5.0, 0.0, VThermHvacMode_HEAT)
    assert algo.boiler_mode == MODE_HELD
    return clock, algo


def test_boiler_hold_keeps_raw_history(monkeypatch) -> None:
    """While held the estimator gets no power, and the learning history still gets the raw reading."""
    clock, algo = _held(monkeypatch)
    clock.advance(60.0)
    algo.calculate(20.0, 19.3, 5.0, 0.0, VThermHvacMode_HEAT)

    assert algo._deadtime_input() == 0.0
    assert algo.dt_est.tin_history[-1][1] == 19.3
    assert algo.dt_est._tin_lp_history[-1][1] == pytest.approx(algo.tint_filter.t_int_lp)
    assert algo.tint_filter.t_int_lp != pytest.approx(19.3)


def test_a_boiler_call_edge_clears_both_histories(monkeypatch) -> None:
    """When the boiler's call changes for the zone, raw and filtered samples of the old regime go."""
    clock, algo = _held(monkeypatch)
    assert algo.dt_est.tin_history
    assert algo.dt_est._tin_lp_history

    algo.update_boiler(CALLED)
    clock.advance(60.0)
    algo.calculate(20.0, 19.5, 5.0, 0.0, VThermHvacMode_HEAT)

    assert algo.boiler_mode != MODE_HELD
    # Only the sample of this tick, taken after the reset, is left.
    assert len(algo.dt_est.tin_history) <= 1
    assert len(algo.dt_est._tin_lp_history) <= 1
