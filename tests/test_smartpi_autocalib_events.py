"""Tests for the AutoCalib events published on the Home Assistant bus."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import valid_entity_id

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.autocalib import AutoCalibTrigger
from custom_components.vtherm_smartpi.smartpi.const import (
    AUTOCALIB_DT_COOL_FALLBACK_DAYS,
    AUTOCALIB_MAX_RETRIES,
    AUTOCALIB_SNAPSHOT_PERIOD_H,
    AutoCalibState,
    SmartPICalibrationPhase,
    SmartPICalibrationResult,
    SmartPIPhase,
)
from custom_components.vtherm_smartpi.smartpi.guards import GuardAction

from fakes.fake_thermostat_runtime import FakeThermostatRuntime
from helpers import force_smartpi_stable_mode

THERMOSTAT_NAME = "Living room"


def _set_reliable_model(algo: MagicMock) -> MagicMock:
    """Make the algorithm stand-in report a reliable, progressing model."""
    algo._last_hvac_mode = VThermHvacMode_HEAT
    algo.phase = SmartPIPhase.STABLE
    algo.calibration_mgr.is_calibrating = False
    algo.calibration_mgr.last_calibration_time = None
    algo.est.tau_reliability.return_value = MagicMock(reliable=True)
    algo.est.learn_ok_count_a = 20
    algo.est.learn_ok_count_b = 20
    algo.est.diag_a_mad_over_med = 0.1
    algo.est.diag_b_mad_over_med = 0.1
    algo.est.a = 0.5
    algo.est.b = 0.02
    algo.dt_est.deadtime_heat_reliable = True
    algo.dt_est.deadtime_cool_reliable = True
    return algo


def _initial_snapshot(ac: AutoCalibTrigger, algo: MagicMock, now: float):
    return ac.check_hourly(now, algo)


def _fallback_snapshot(ac: AutoCalibTrigger, algo: MagicMock, now: float):
    algo.dt_est.deadtime_cool_reliable = False
    ac._initial_reliables_ts = now - (AUTOCALIB_DT_COOL_FALLBACK_DAYS + 1) * 86400.0
    return ac.check_hourly(now, algo)


def _rolling_snapshot(ac: AutoCalibTrigger, algo: MagicMock, now: float):
    ac._state = AutoCalibState.MONITORING
    ac._snapshot_ts = now - (AUTOCALIB_SNAPSHOT_PERIOD_H + 1) * 3600.0
    ac._snap_ok_count_a = 10
    ac._snap_ok_count_b = 10
    return ac.check_hourly(now, algo, ext_temp=5.0, current_temp=20.0)


def _manual_trigger(ac: AutoCalibTrigger, algo: MagicMock, now: float):
    return ac.force_manual_trigger(now, algo)


def _arm_calibration_exit(
    ac: AutoCalibTrigger,
    algo: MagicMock,
    *,
    new_b_obs: int,
    retry_count: int = 0,
) -> None:
    """Put the supervisor in TRIGGERED state with the given post-calibration progress."""
    ac._state = AutoCalibState.TRIGGERED
    ac._triggered_params = ["a", "b"]
    ac._retry_count = retry_count
    ac._post_calib_snap_a = algo.est.learn_ok_count_a - 10
    ac._post_calib_snap_b = algo.est.learn_ok_count_b - new_b_obs


def _positive_exit(ac: AutoCalibTrigger, algo: MagicMock, now: float):
    _arm_calibration_exit(ac, algo, new_b_obs=10)
    return ac.on_calibration_complete(now, algo)


def _partial_exit(ac: AutoCalibTrigger, algo: MagicMock, now: float):
    _arm_calibration_exit(ac, algo, new_b_obs=0)
    return ac.on_calibration_complete(now, algo)


def _max_retries_exit(ac: AutoCalibTrigger, algo: MagicMock, now: float):
    _arm_calibration_exit(ac, algo, new_b_obs=0, retry_count=AUTOCALIB_MAX_RETRIES - 1)
    return ac.on_calibration_complete(now, algo)


@pytest.mark.parametrize(
    ("build_event", "event_type", "expected"),
    [
        (_initial_snapshot, "smartpi_autocalib_snapshot_taken", {"reason": "initial"}),
        (_fallback_snapshot, "smartpi_autocalib_snapshot_taken", {"reason": "fallback_7d"}),
        (_rolling_snapshot, "smartpi_autocalib_snapshot_taken", {"reason": "rolling"}),
        (_manual_trigger, "smartpi_autocalib_triggered", {"triggered_params": ["manual_request"]}),
        (_positive_exit, "smartpi_autocalib_success", {"improved_params": ["a", "b"]}),
        (_partial_exit, "smartpi_autocalib_retry", {"retry_count": 1}),
        (_max_retries_exit, "smartpi_autocalib_degraded", {"retry_count": AUTOCALIB_MAX_RETRIES}),
    ],
    ids=[
        "initial_snapshot",
        "fallback_snapshot",
        "rolling_snapshot",
        "manual_trigger",
        "positive_exit",
        "partial_exit",
        "max_retries_exit",
    ],
)
def test_autocalib_event_payload_names_thermostat(build_event, event_type, expected):
    """AutoCalib payloads carry the thermostat name, never a fake entity id."""
    ac = AutoCalibTrigger(THERMOSTAT_NAME)

    event = build_event(ac, _set_reliable_model(MagicMock()), time.time())

    assert event is not None
    assert event.event_type == event_type
    assert event.payload["name"] == THERMOSTAT_NAME
    assert "entity_id" not in event.payload
    assert {key: event.payload[key] for key in expected} == expected


def _fired_data(fire: MagicMock, event_type: str) -> dict:
    """Return the data of the single bus event of the given type."""
    fired = [call.args[1] for call in fire.call_args_list if call.args[0] == event_type]
    assert len(fired) == 1
    return fired[0]


def _assert_identifies_thermostat(data: dict, thermostat: FakeThermostatRuntime) -> None:
    """The bus event must name the climate entity, as the recorder filter expects."""
    assert valid_entity_id(data["entity_id"])
    assert data["entity_id"] == thermostat.entity_id
    assert data["name"] == thermostat.name


def _control_algo(thermostat: FakeThermostatRuntime) -> MagicMock:
    """Return a SmartPI stand-in that lets control_heating reach the AutoCalib checks."""
    algo = MagicMock(spec=SmartPI)
    algo.autocalib = AutoCalibTrigger(thermostat.name)
    algo.calibration_state = SmartPICalibrationPhase.IDLE
    algo.calibration_mgr = MagicMock()
    algo.calibration_mgr.calibration_result = SmartPICalibrationResult.SUCCESS
    algo.est = MagicMock()
    algo.dt_est = MagicMock()
    algo.gov = MagicMock()
    algo.guards = MagicMock()
    algo.guards.check_guard_cut.return_value = GuardAction.NONE
    algo.guards.check_guard_kick.return_value = GuardAction.NONE
    algo.deadband_mgr = MagicMock()
    algo.deadband_mgr.deadband_changed = False
    algo.deadband_mgr.near_band_changed = False
    algo.in_near_band = False
    algo.calculated_on_percent = 0.0
    algo.committed_on_percent = 0.0
    algo.on_percent = 0.0
    thermostat.prop_algorithm = _set_reliable_model(algo)
    return algo


async def test_hourly_check_event_identifies_thermostat_entity(fake_runtime) -> None:
    """The hourly AutoCalib check fires its event with the climate entity id."""
    _control_algo(fake_runtime)
    handler = SmartPIHandler(fake_runtime)

    await handler.control_heating()

    data = _fired_data(fake_runtime.hass.bus.async_fire, "smartpi_autocalib_snapshot_taken")
    _assert_identifies_thermostat(data, fake_runtime)
    assert data["reason"] == "initial"


async def test_calibration_exit_event_identifies_thermostat_entity(fake_runtime) -> None:
    """The end of a supervised calibration fires its event with the climate entity id."""
    algo = _control_algo(fake_runtime)
    _arm_calibration_exit(algo.autocalib, algo, new_b_obs=10)
    # Keep the hourly check rate-limited so only the exit event is fired.
    algo.autocalib._last_hourly_check_ts = time.time()
    handler = SmartPIHandler(fake_runtime)
    handler._prev_is_calibrating = True

    await handler.control_heating()

    data = _fired_data(fake_runtime.hass.bus.async_fire, "smartpi_autocalib_success")
    _assert_identifies_thermostat(data, fake_runtime)


async def test_forced_calibration_event_identifies_thermostat_entity(fake_runtime) -> None:
    """The manual calibration service fires its AutoCalib event with the climate entity id."""
    algo = SmartPI(
        hass=fake_runtime.hass,
        cycle_min=10,
        minimal_activation_delay=0,
        minimal_deactivation_delay=0,
        name=fake_runtime.name,
    )
    force_smartpi_stable_mode(algo)
    fake_runtime.prop_algorithm = algo
    handler = SmartPIHandler(fake_runtime)
    handler.control_heating = AsyncMock()
    handler.update_attributes = MagicMock()

    await handler.service_force_smartpi_calibration()

    data = _fired_data(fake_runtime.hass.bus.async_fire, "smartpi_autocalib_triggered")
    _assert_identifies_thermostat(data, fake_runtime)
    assert data["triggered_params"] == ["manual_request"]
