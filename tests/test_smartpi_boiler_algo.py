"""SmartPI under a boiler that is not called: it learns how the zone cools, nothing that needs heat."""

from unittest.mock import MagicMock

import pytest

import custom_components.vtherm_smartpi.algo as algo_module
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.boiler_call import (
    GATE_BOILER_SETTLING,
    MODE_COOLING,
    MODE_HELD,
    MODE_WARMING,
    SOURCE_SMART_BOILER,
    BoilerReading,
)
from custom_components.vtherm_smartpi.smartpi.const import BOILER_CALL_GRACE_S, HEATING_RADIATOR
from custom_components.vtherm_smartpi.smartpi.diagnostics import build_published_diagnostics
from tests.helpers import force_smartpi_stable_mode

NOT_CALLED = BoilerReading(False, "no_demand", SOURCE_SMART_BOILER)
CALLED = BoilerReading(True, None, SOURCE_SMART_BOILER)


class _Clock:
    def __init__(self, monkeypatch):
        self.now = 1000.0
        monkeypatch.setattr(algo_module.time, "monotonic", lambda: self.now)

    def advance(self, seconds):
        self.now += seconds


def _algo() -> SmartPI:
    algo = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="TestBoiler"
    )
    algo.configure_heating_type(HEATING_RADIATOR)
    force_smartpi_stable_mode(algo)
    return algo


def _run(algo, temp=19.5):
    algo.calculate(20.0, temp, 5.0, 0.0, VThermHvacMode_HEAT)


def _held(monkeypatch):
    """A zone calling under a boiler not called for longer than the emitter settles."""
    clock = _Clock(monkeypatch)
    algo = _algo()
    algo.update_boiler(NOT_CALLED)
    algo._set_committed_actuator_output(0.4)
    _run(algo)
    clock.advance(algo.boiler.settle_s)
    _run(algo)
    assert algo.boiler_mode == MODE_HELD
    return clock, algo


def test_without_a_reading_nothing_changes(monkeypatch):
    _Clock(monkeypatch)
    algo = _algo()
    algo.update_boiler(None)
    _run(algo)
    assert algo.boiler_mode is None
    assert "boiler" not in build_published_diagnostics(algo)


def test_learning_sees_no_heat_under_a_boiler_that_is_not_called(monkeypatch):
    clock, algo = _held(monkeypatch)
    learned = []
    monkeypatch.setattr(algo, "update_learning", lambda **kwargs: learned.append(kwargs["u_active"]))
    dead_time = MagicMock()
    monkeypatch.setattr(algo.dt_est, "update", dead_time)

    clock.advance(60.0)
    _run(algo)

    assert learned == [0.0]
    assert algo._committed_on_percent == pytest.approx(0.4)
    # The dead-time estimator keeps the temperature for the learning windows, but sees no step.
    assert [call.kwargs["u_applied"] for call in dead_time.call_args_list] == [0.0]
    assert build_published_diagnostics(algo)["boiler"]["learning"] == MODE_HELD


def test_the_feed_forward_trim_is_frozen_under_a_boiler_that_is_not_called(monkeypatch):
    """With nothing else freezing it (a trusted model, normal regulation)."""
    from custom_components.vtherm_smartpi.smartpi.const import ABConfidenceState, GovernanceRegime

    clock, algo = _held(monkeypatch)
    monkeypatch.setattr(algo._ab_confidence, "evaluate", lambda *args, **kwargs: ABConfidenceState.AB_OK)
    algo._ab_confidence.state = ABConfidenceState.AB_OK
    monkeypatch.setattr(algo.gov, "determine_regime", lambda *args, **kwargs: GovernanceRegime.EXCITED_STABLE)
    algo._ff_v2_reboot_freeze_remaining = 0

    clock.advance(60.0)
    _run(algo)
    assert algo._ff_trim.frozen and algo._ff_trim.freeze_reason == "boiler_not_heating"

    algo.update_boiler(CALLED)
    clock.advance(60.0)
    _run(algo)
    assert algo._ff_trim.frozen  # the emitter warms
    clock.advance(algo.boiler.settle_s)
    _run(algo)
    assert not algo._ff_trim.frozen


def test_learning_waits_while_the_emitter_cools_after_an_unanswered_call(monkeypatch):
    clock = _Clock(monkeypatch)
    algo = _algo()
    learned = []
    monkeypatch.setattr(algo, "update_learning", lambda **kwargs: learned.append(kwargs["u_active"]))
    algo.update_boiler(NOT_CALLED)
    algo._set_committed_actuator_output(0.4)
    _run(algo)
    clock.advance(60.0)
    _run(algo)
    # A switched device: its 10-min cycle is added to the grace.
    clock.advance(BOILER_CALL_GRACE_S + 10 * 60.0)
    _run(algo)
    assert algo.boiler_mode == MODE_COOLING
    assert algo.learning_gate == GATE_BOILER_SETTLING
    # Within the grace the boiler may still answer: learning as usual; then it waits.
    assert learned == [pytest.approx(0.4)]


def test_after_a_hold_the_boiler_s_start_is_no_step_and_learning_waits_the_warm_up(monkeypatch):
    clock, algo = _held(monkeypatch)
    learned = []
    monkeypatch.setattr(algo, "update_learning", lambda **kwargs: learned.append(kwargs["u_active"]))
    reset = MagicMock(wraps=algo.dt_est.reset_observation)
    monkeypatch.setattr(algo.dt_est, "reset_observation", reset)

    algo.update_boiler(CALLED)
    clock.advance(60.0)
    _run(algo)
    assert algo.boiler_mode == MODE_WARMING and algo.learning_gate == GATE_BOILER_SETTLING
    assert learned == []
    # The estimator starts again from the command: the boiler's start is not timed.
    assert reset.call_args.args[0] == pytest.approx(algo._deadtime_power())

    clock.advance(algo.boiler.settle_s)
    _run(algo)
    assert algo.boiler_mode is None and algo.learning_gate is None
    assert learned == [pytest.approx(0.4)]


def test_held_with_only_the_long_term_water_average_learns_no_heat(monkeypatch):
    """A momentary gap of the water data gives the factor of heating days: it is no heat."""
    from custom_components.vtherm_smartpi.smartpi.water_input import SOURCE_AVERAGE, SOURCE_LIVE, WaterState

    clock, algo = _held(monkeypatch)
    algo.water.state = WaterState(uses_water=True, factor=0.84, source=SOURCE_AVERAGE)
    assert algo._learning_power() == 0.0
    algo.water.state = WaterState(uses_water=True, factor=0.02, source=SOURCE_LIVE)
    assert algo._learning_power() == pytest.approx(algo._committed_on_percent)


def test_pausing_learning_while_held_restarts_the_dead_time_estimator_from_its_input(monkeypatch):
    _clock, algo = _held(monkeypatch)
    reset = MagicMock()
    monkeypatch.setattr(algo.dt_est, "reset_observation", reset)
    algo.set_learning_enabled(False)
    assert reset.call_args.args[0] == 0.0


def test_a_calibration_is_cancelled_while_the_boiler_is_not_called(monkeypatch):
    clock, algo = _held(monkeypatch)
    algo.calibration_mgr.start_calibration(now=clock.now, is_manual=True)
    assert algo.calibration_mgr.is_calibrating
    clock.advance(60.0)
    _run(algo)
    assert not algo.calibration_mgr.is_calibrating


def test_a_switched_device_gets_its_cycle_added_to_the_grace(monkeypatch):
    clock = _Clock(monkeypatch)
    algo = _algo()
    graces = []
    original = algo.boiler.update
    monkeypatch.setattr(
        algo.boiler, "update", lambda **kwargs: graces.append(kwargs["grace_s"]) or original(**kwargs)
    )
    _run(algo)
    assert graces == [pytest.approx(BOILER_CALL_GRACE_S + 10 * 60.0)]


def test_working_as_another_thermostat_s_source_clears_the_boiler_s_mode(monkeypatch):
    clock, algo = _held(monkeypatch)
    algo._executive = {"lead": "lead-id", "command": 0.3}
    clock.advance(60.0)
    _run(algo)
    assert algo.boiler_mode is None


async def test_a_cycle_under_a_boiler_that_is_not_called_records_no_heat(monkeypatch):
    _clock, algo = _held(monkeypatch)
    record = MagicMock()
    monkeypatch.setattr(algo.dependency_model, "record_realized_power", record)

    await algo.on_cycle_completed(e_eff=0.4, elapsed_ratio=1.0, cycle_duration_min=10.0)

    assert record.call_args.args[2] == 0.0


def test_the_dependency_layer_is_told_that_no_heat_comes(monkeypatch):
    from custom_components.vtherm_smartpi.smartpi.dependency_inputs import DependencyReadings

    clock, algo = _held(monkeypatch)
    contexts = []
    monkeypatch.setattr(algo.dependency_model, "update", lambda readings, ctx: contexts.append(ctx))
    algo.set_known_inputs_provider(lambda: DependencyReadings())
    clock.advance(60.0)
    _run(algo)
    assert contexts and contexts[-1].heat_withheld is True
