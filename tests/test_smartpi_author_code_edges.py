"""Edge paths of the author's code that the reference governor's stage left untested.

Taking over the author's reference governor (stage C1) changed which paths of
some of the author's files the suite runs. These tests run such paths
directly, so no file of the author falls below the coverage it had before:
the drift detector's rejections, the estimator's convergence checks, the PI
controller's snapshot and hold edges, the FF3 open-loop prediction's invalid
inputs, the FF trim observers' guards, and the learning window's accessors.
"""

from __future__ import annotations

import pytest

from custom_components.vtherm_smartpi.smartpi.ab_drift import (
    DriftCandidate,
    DriftChannelState,
    detect_persistent_drift,
)
from custom_components.vtherm_smartpi.smartpi.ab_estimator import ABEstimator
from custom_components.vtherm_smartpi.smartpi.const import (
    AB_B_CONVERGENCE_MIN_SAMPLES,
    SMARTPI_RECALC_INTERVAL_SEC,
)
from custom_components.vtherm_smartpi.smartpi.controller import SmartPIController
from custom_components.vtherm_smartpi.smartpi.ff3_predictor import (
    FF3OpenLoopPrediction,
    compute_ff3_action_sensitivity,
    predict_ff3_open_loop,
)
from custom_components.vtherm_smartpi.smartpi.ff_trim import CausalFFTrimObserver
from custom_components.vtherm_smartpi.smartpi.ff_trim_periodic import PeriodicFFTrimObserver
from custom_components.vtherm_smartpi.smartpi.learning_window import LearningWindowManager
from custom_components.vtherm_smartpi.smartpi.thermal_twin_1r1c import ThermalTwin1R1C

DRIFT = dict(hist_center=0.0, hist_mad_eff=1.0, min_count=3, max_buffer_mad_factor=2.0, min_shift_factor=2.0)


def _channel(*candidates: tuple[float, int]) -> DriftChannelState:
    channel = DriftChannelState()
    for value, seq in candidates:
        channel.drift_buffer.append(DriftCandidate(value=value, seq=seq, side=1 if value > 0 else -1))
    return channel


def test_drift_candidates_too_far_apart_are_not_a_drift() -> None:
    channel = _channel((3.0, 1), (3.1, 2), (3.0, 9))

    detected, info = detect_persistent_drift(channel=channel, seq_gap_max=3, **DRIFT)

    assert not detected
    assert info["reason"] == "DRIFT_NOT_ENOUGH_CANDIDATES"


def test_a_drift_shift_smaller_than_the_history_spread_is_not_a_drift() -> None:
    channel = _channel((1.0, 1), (1.05, 2), (1.0, 3))

    detected, info = detect_persistent_drift(channel=channel, seq_gap_max=3, **DRIFT)

    assert not detected
    assert info["reason"] == "DRIFT_SHIFT_TOO_SMALL"
    assert channel.last_shift == pytest.approx(1.0)


def _converging(values: list[float]) -> ABEstimator:
    estimator = ABEstimator()
    estimator.learn_ok_count_b = AB_B_CONVERGENCE_MIN_SAMPLES
    estimator._b_hat_hist.extend(values)
    return estimator


@pytest.mark.parametrize(
    "values",
    [
        [0.0] * 6,
        [0.001, 0.002, 0.004, 0.001, 0.0025, 0.004],
        [0.0010, 0.0010, 0.0010, 0.0010, 0.0012, 0.0010],
    ],
    ids=["zero_median", "dispersed", "recent_range_too_wide"],
)
def test_b_is_not_converged_for_a_on_a_doubtful_history(values) -> None:
    assert _converging(values).b_converged_for_a() is False


def test_b_with_a_steady_history_is_converged_for_a() -> None:
    assert _converging([0.001] * 6).b_converged_for_a() is True


def test_restoring_p_needs_a_pi_snapshot() -> None:
    controller = SmartPIController("edges")

    with pytest.raises(ValueError):
        controller.restore_proportional_reference(0.2)


def test_the_legacy_servo_hold_flag_and_an_empty_hold() -> None:
    controller = SmartPIController("edges")
    controller.start_integral_hold("none")
    assert not controller.integral_hold_active

    controller.servo_integral_hold = True
    assert controller.servo_integral_hold
    controller.servo_integral_hold = False
    assert not controller.integral_hold_active

    controller.start_integral_hold("other")
    controller.servo_integral_hold = False
    assert controller.integral_hold_mode == "other"


def _predict(twin: ThermalTwin1R1C, **overrides) -> FF3OpenLoopPrediction:
    values = dict(
        twin=twin,
        current_temp=19.0,
        ext_temp=5.0,
        a=0.02,
        b=0.002,
        u_first_cycle=0.6,
        u_base=0.4,
        cycle_min=10.0,
        deadtime_heat_s=600.0,
        horizon_cycles=3,
    )
    values.update(overrides)
    return predict_ff3_open_loop(**values)


def test_ff3_open_loop_prediction_edges() -> None:
    twin = ThermalTwin1R1C(dt_s=SMARTPI_RECALC_INTERVAL_SEC, gamma=0.1)

    assert _predict(twin, a=-0.02).status == "invalid_params"
    # A twin without a command history starts from the base command.
    prediction = _predict(twin)
    assert prediction.status == "ok"
    assert prediction.effective_powers[0] == pytest.approx(0.4)
    assert _predict(twin, ext_temp=float("inf")).status == "invalid_params"
    # A model so strong that the prediction overflows.
    assert _predict(twin, a=1e308, b=1e-300).status == "invalid_prediction"
    assert compute_ff3_action_sensitivity([prediction]) == 0.0


@pytest.mark.parametrize(
    ("hvac_mode", "a", "b", "reason"),
    [
        ("heat", float("nan"), 0.002, "invalid_model"),
        ("heat", 0.0, 0.002, "invalid_model_sign"),
        ("heat", -0.02, 0.002, "invalid_model_sign"),
        ("cool", 0.02, 0.002, "invalid_model_sign"),
        ("fan_only", 0.02, 0.002, "invalid_hvac_mode"),
        ("heat", 0.02, 0.002, None),
    ],
)
def test_the_ff_trim_observer_checks_the_model(hvac_mode, a, b, reason) -> None:
    assert CausalFFTrimObserver._model_rejection_reason(hvac_mode, a, b) == reason


def test_the_ff_trim_observer_reads_power_from_its_trace() -> None:
    observer = CausalFFTrimObserver(10.0)

    assert observer._active_ownership_segments == observer.physical_trace.active_ownership_segments
    power, coverage = observer._mean_causal_power(0.0, 600.0)
    assert power is None
    assert coverage == 0.0


def test_a_periodic_window_waits_for_causal_power() -> None:
    observer = PeriodicFFTrimObserver(10.0)

    assert observer.try_close_window(earliest_power_start=None, deadtime_s=600.0, deadtime_reliable=True) is None
    assert observer.state == "warming_up"
    assert observer.last_reject_reason == "causal_power_not_covered"


def test_learning_window_accessors_and_a_bad_saved_date() -> None:
    window = LearningWindowManager("edges")
    assert window._u_std == 0.0
    window._T_int_start, window._T_ext_start = 19.5, 4.0
    assert (window.T_int_start, window.T_ext_start) == (19.5, 4.0)

    window.load_state({"learning_start_date": "not a date"})

    assert window.learning_start_date is not None
