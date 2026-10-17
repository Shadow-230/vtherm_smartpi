"""SmartPI with the water input: command and learning in power at the reference water."""

from unittest.mock import MagicMock

import pytest

from custom_components.vtherm_smartpi.algo import WATER_INTEGRAL_HOLD, SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import (
    HEATING_ELECTRIC_FAST,
    HEATING_RADIATOR,
)
from custom_components.vtherm_smartpi.smartpi.diagnostics import build_published_diagnostics
from custom_components.vtherm_smartpi.smartpi.water_input import WaterReading, emitter_factor
from tests.helpers import force_smartpi_stable_mode


def make_smartpi(heating_type=HEATING_RADIATOR, **kwargs):
    defaults = dict(
        hass=MagicMock(),
        cycle_min=10,
        minimal_activation_delay=0,
        minimal_deactivation_delay=0,
        name="TestWater",
    )
    defaults.update(kwargs)
    algo = SmartPI(**defaults)
    algo.configure_heating_type(heating_type)
    return algo


def live(supply_c, heat_reason=None):
    return WaterReading(supply_c=supply_c, heat_available=heat_reason is None, heat_reason=heat_reason)


def run(algo, temp=19.0, target=20.0, ext=5.0):
    algo.calculate(target, temp, ext, 0.0, VThermHvacMode_HEAT)


def test_without_water_data_nothing_changes():
    algo = make_smartpi()
    algo.update_water(None, 19.0)
    assert algo.water_factor == 1.0
    assert algo._to_actuator_power(0.4) == pytest.approx(0.4)
    assert algo._to_linear_power(0.4) == pytest.approx(0.4)
    assert "water" not in algo.save_state()


def test_command_is_power_over_the_factor_and_learning_sees_command_times_factor():
    algo = make_smartpi()
    algo.update_water(live(40.0), 20.0)
    factor = emitter_factor(HEATING_RADIATOR, 40.0, 20.0)
    assert algo.water_factor == pytest.approx(factor)
    assert algo._to_actuator_power(0.2) == pytest.approx(0.2 / factor)
    assert algo._to_actuator_power(0.9) == 1.0
    assert algo._to_linear_power(0.5) == pytest.approx(0.5 * factor)
    algo._set_linear_output(0.2)
    assert algo.on_percent == pytest.approx(0.2 / factor)


def test_a_new_factor_updates_the_committed_power_seen_by_learning():
    algo = make_smartpi()
    algo.update_water(live(60.0), 20.0)
    algo._set_committed_actuator_output(0.5)
    assert algo._committed_on_percent == pytest.approx(0.5)
    for _ in range(200):
        algo.update_water(live(40.0), 20.0)
    algo.water._supply_avg = 40.0
    algo.update_water(live(40.0), 20.0)
    assert algo._committed_on_percent == pytest.approx(0.5 * algo.water_factor)


def test_power_is_capped_by_the_water_and_flagged_too_cold():
    algo = make_smartpi()
    algo.update_water(live(40.0), 20.0)
    factor = algo.water_factor
    limited = algo._apply_soft_constraints(0.9, 0.0, True)
    assert limited == pytest.approx(factor)
    assert algo.on_percent == pytest.approx(1.0)
    assert algo.water_diagnostics()["water_too_cold"] is True
    algo._apply_soft_constraints(0.1, 0.0, True)
    assert algo.water_diagnostics()["water_too_cold"] is False


class _Clock:
    def __init__(self, monkeypatch):
        import custom_components.vtherm_smartpi.algo as algo_module

        self.now = 1000.0
        monkeypatch.setattr(algo_module.time, "monotonic", lambda: self.now)

    def advance(self, seconds):
        self.now += seconds


def _settled(clock, algo, temp=19.0):
    """Two calculations a few minutes apart, the controller running out of its deadband."""
    run(algo, temp=temp)
    clock.advance(300.0)
    run(algo, temp=temp)


def test_hot_water_holds_the_command_and_the_integral(monkeypatch):
    clock = _Clock(monkeypatch)
    # Control: the same algorithm without hot water is not under the water's hold.
    free = make_smartpi()
    force_smartpi_stable_mode(free)
    free.update_water(live(50.0), 20.0)
    _settled(clock, free)
    assert free.ctl.last_i_mode != f"I:HOLD({WATER_INTEGRAL_HOLD})"

    algo = make_smartpi()
    force_smartpi_stable_mode(algo)
    algo.update_water(live(50.0), 20.0)
    _settled(clock, algo)
    algo._set_committed_actuator_output(0.6)
    held = algo._actuator_committed_on_percent
    state = algo.update_water(live(70.0, heat_reason="dhw_active"), 20.0)
    assert state.no_heat
    integral = algo.ctl.integral
    clock.advance(300.0)
    run(algo, temp=19.0)
    assert algo.ctl.last_i_mode == f"I:HOLD({WATER_INTEGRAL_HOLD})"
    assert algo.ctl.integral == integral
    assert algo.on_percent == pytest.approx(held)
    # Learning and the dead-time estimator see no heat while the water brings nothing.
    assert algo._to_linear_power(algo._actuator_committed_on_percent) == 0.0
    assert algo._deadtime_power() == 0.0
    # A demand of zero (off, satisfied, shed, cut) still gives zero.
    algo._set_linear_output(0.0)
    assert algo.on_percent == 0.0
    # Another path may release the hold; the next water update re-asserts it.
    algo.ctl.clear_integral_hold()
    algo.update_water(live(70.0, heat_reason="dhw_active"), 20.0)
    assert algo.ctl.integral_hold_mode == WATER_INTEGRAL_HOLD
    # Heat comes back: the hold ends and the command follows the controller again.
    algo.update_water(live(50.0), 20.0)
    assert algo.ctl.integral_hold_mode == "none"
    assert algo._water_hold_actuator is None


def test_cold_water_from_an_idle_boiler_still_calls_for_heat(monkeypatch):
    """A boiler that fires on demand: cold water must not lock the zone."""
    clock = _Clock(monkeypatch)
    algo = make_smartpi()
    force_smartpi_stable_mode(algo)
    for supply in (15.0, 20.0):  # below the room, then barely above it
        state = algo.update_water(live(supply), 17.0)
        assert state.low_heat and not state.no_heat
        _settled(clock, algo, temp=17.0)
        assert algo.ctl.integral_hold_mode == WATER_INTEGRAL_HOLD
        assert algo._water_hold_actuator is None
        assert algo.on_percent == pytest.approx(1.0)
    # The boiler fires, the water warms: the integral runs again.
    algo.update_water(live(55.0), 17.0)
    assert algo.ctl.integral_hold_mode == "none"


def test_the_learning_phase_commits_the_power_and_times_the_command():
    algo = make_smartpi()
    algo.update_water(live(40.0), 20.0)
    factor = algo.water_factor
    algo._set_linear_output(1.0)  # bang-bang of the learning phase
    algo._commit_current_linear_output()
    assert algo._committed_on_percent == pytest.approx(factor)
    assert algo._deadtime_power() == pytest.approx(1.0)


@pytest.mark.parametrize("supply", [21.0, 22.0, 40.0])
def test_the_committed_command_survives_the_round_trip(supply):
    """Below WATER_NO_HEAT command and power are not inverse: the command stays the truth."""
    algo = make_smartpi()
    algo.update_water(live(supply), 20.0)
    algo._set_linear_output(1.0)
    algo._commit_current_linear_output()
    assert algo._actuator_committed_on_percent == pytest.approx(1.0)
    assert algo.committed_on_percent == pytest.approx(1.0)
    assert algo._deadtime_power() == pytest.approx(1.0)
    assert algo._committed_on_percent == pytest.approx(algo.water_factor)
    # A new factor keeps the command and moves the power.
    algo.update_water(live(supply + 10.0), 20.0)
    algo._sync_actuator_committed_on_percent()
    assert algo.committed_on_percent == pytest.approx(1.0)


def test_the_hot_water_hold_reports_the_held_command():
    algo = make_smartpi()
    algo.update_water(live(50.0), 20.0)
    algo._set_committed_actuator_output(0.6)
    algo.update_water(live(70.0, heat_reason="dhw_active"), 20.0)
    assert algo.committed_on_percent == pytest.approx(0.6)
    algo._set_linear_output(0.3)
    algo._commit_current_linear_output()
    assert algo.committed_on_percent == pytest.approx(0.6)
    assert algo._committed_on_percent == 0.0


def test_the_activation_delay_applies_to_the_command(monkeypatch):
    algo = make_smartpi(minimal_activation_delay=60)
    algo.update_water(live(31.0), 20.0)
    factor = algo.water_factor
    power = 0.26 * factor  # a command of 26 %: 156 s of a 10-min cycle
    assert algo.update_timing_constraints(0.0, power) == pytest.approx(power)
    assert not algo._last_forced_by_timing
    assert algo.update_timing_constraints(0.0, 0.05 * factor) == 0.0
    assert algo._last_forced_by_timing


def test_the_feed_forward_trim_records_the_power(monkeypatch):
    algo = make_smartpi()
    algo.update_water(live(40.0), 20.0)
    recorded = {}
    monkeypatch.setattr(algo._causal_power_trace, "complete_applied_cycle", lambda **kw: recorded.update(kw))
    algo._record_completed_fftrim_power(cycle_end_now=0.0, e_eff=0.6)
    assert recorded["realized_linear_power"] == pytest.approx(0.6 * algo.water_factor)


def test_a_hold_released_since_the_last_water_update_is_restored_before_the_controller(monkeypatch):
    """Another path that releases the hold between two water updates does not let the integral move."""
    clock = _Clock(monkeypatch)
    algo = make_smartpi()
    force_smartpi_stable_mode(algo)
    algo.update_water(live(50.0), 20.0)
    _settled(clock, algo)
    algo.update_water(live(70.0, heat_reason="dhw_active"), 20.0)
    integral = algo.ctl.integral
    algo.ctl.clear_integral_hold()

    clock.advance(300.0)
    run(algo, temp=19.0)

    assert algo.ctl.last_i_mode == f"I:HOLD({WATER_INTEGRAL_HOLD})"
    assert algo.ctl.integral == integral


def test_hot_water_does_not_release_another_hold():
    algo = make_smartpi()
    algo.update_water(live(50.0), 20.0)
    algo.ctl.start_integral_hold("servo_recovery")
    algo.update_water(live(70.0, heat_reason="dhw_active"), 20.0)
    assert algo.ctl.integral_hold_mode == "servo_recovery"
    algo.update_water(live(50.0), 20.0)
    assert algo.ctl.integral_hold_mode == "servo_recovery"


def test_electric_heating_type_ignores_the_water():
    algo = make_smartpi(HEATING_ELECTRIC_FAST)
    algo.update_water(live(30.0), 20.0)
    assert algo.water_factor == 1.0
    assert algo.water_diagnostics() is None
    assert "water" not in build_published_diagnostics(algo)


def test_diagnostics_and_persistence_of_a_water_type():
    algo = make_smartpi()
    for _ in range(5):
        algo.update_water(live(45.0), 20.0)
    diag = build_published_diagnostics(algo)["water"]
    assert diag["source"] == "live"
    assert diag["factor"] == pytest.approx(algo.water_factor, abs=1e-3)
    assert diag["supply_avg_c"] == pytest.approx(45.0, abs=0.1)
    saved = algo.save_state()
    assert saved["water"]["availability"] == "used"
    restored = make_smartpi()
    restored.load_state(saved)
    state = restored.update_water(WaterReading(supply_reason="flow_stale"), 20.0)
    assert state.source == "average"
    assert restored.water_factor == pytest.approx(algo.water._long_avg)


def test_reset_learning_keeps_the_water_average():
    algo = make_smartpi()
    algo.update_water(live(45.0), 20.0)
    average = algo.water._long_avg
    algo.reset_learning()
    assert algo.water._long_avg == average


def test_the_valve_s_activation_floor_applies_to_the_command_too():
    """A valve whose command would stay open shorter than the activation delay opens for the
    delay: the floor is the valve's command, so the power is that command times the water."""
    algo = make_smartpi(minimal_activation_delay=60)
    algo.configure_valve_linearization(False, valve_mode=True)
    algo.update_water(live(31.0), 20.0)
    factor = algo.water_factor
    assert factor < 1.0

    power = algo.update_timing_constraints(0.0, 0.05 * factor)  # a command of 5 %: 30 s of 600

    assert power == pytest.approx(0.1 * factor)  # the command raised to 60 s of 600
    assert algo.forced_by_timing
