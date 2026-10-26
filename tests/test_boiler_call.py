"""SmartPI pauses learning while it calls for heat below the central boiler threshold (documentation/en/technical_doc.md, 3.7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

import custom_components.vtherm_smartpi.algo as algo_module
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.boiler_call import (
    VT_ACTIVE_COUNT_UNIQUE_ID,
    VT_ACTIVE_POWER_UNIQUE_ID,
    VT_COUNT_THRESHOLD_UNIQUE_ID,
    VT_DOMAIN,
    VT_POWER_THRESHOLD_UNIQUE_ID,
    central_boiler_threshold_reached,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.ab_estimator import ABEstimator
from custom_components.vtherm_smartpi.smartpi.const import SmartPICalibrationPhase
from custom_components.vtherm_smartpi.smartpi.diagnostics import build_published_diagnostics
from fakes.fake_thermostat_runtime import FakeThermostatRuntime
from helpers import force_smartpi_stable_mode


# --- reading Versatile Thermostat's central boiler threshold -------------------


def _vt_entity(hass, domain: str, unique_id: str, state: str, unit: str | None = None) -> None:
    entry = er.async_get(hass).async_get_or_create(domain, VT_DOMAIN, unique_id, suggested_object_id=unique_id)
    hass.states.async_set(entry.entity_id, state, {"unit_of_measurement": unit} if unit else {})


async def test_threshold_unknown_without_values(hass) -> None:
    assert central_boiler_threshold_reached(hass) is None


async def test_threshold_unknown_when_unavailable(hass) -> None:
    _vt_entity(hass, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID, "unavailable")
    _vt_entity(hass, "number", VT_COUNT_THRESHOLD_UNIQUE_ID, "3")

    assert central_boiler_threshold_reached(hass) is None


@pytest.mark.parametrize(
    ("count", "count_threshold", "reached"),
    [("3", "3", True), ("2", "3", False), ("5", "0", False)],
)
async def test_count_threshold(hass, count, count_threshold, reached) -> None:
    """Versatile Thermostat's rule: a threshold counts when it is above 0 and the count reaches it."""
    _vt_entity(hass, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID, count)
    _vt_entity(hass, "number", VT_COUNT_THRESHOLD_UNIQUE_ID, count_threshold)

    assert central_boiler_threshold_reached(hass) is reached


@pytest.mark.parametrize(
    ("power", "power_unit", "threshold", "threshold_unit", "reached"),
    [
        ("2000", "W", "3000", "W", False),
        ("3000", "W", "3000", "W", True),
        ("3.5", "kW", "3000", "W", True),
        ("2.5", "kW", "3", "kW", False),
        ("9000", "W", "0", "W", False),
    ],
)
async def test_power_threshold(hass, power, power_unit, threshold, threshold_unit, reached) -> None:
    _vt_entity(hass, "sensor", VT_ACTIVE_POWER_UNIQUE_ID, power, power_unit)
    _vt_entity(hass, "number", VT_POWER_THRESHOLD_UNIQUE_ID, threshold, threshold_unit)

    assert central_boiler_threshold_reached(hass) is reached


async def test_either_threshold_is_enough(hass) -> None:
    _vt_entity(hass, "sensor", VT_ACTIVE_COUNT_UNIQUE_ID, "1")
    _vt_entity(hass, "number", VT_COUNT_THRESHOLD_UNIQUE_ID, "3")
    _vt_entity(hass, "sensor", VT_ACTIVE_POWER_UNIQUE_ID, "4000", "W")
    _vt_entity(hass, "number", VT_POWER_THRESHOLD_UNIQUE_ID, "3000", "W")

    assert central_boiler_threshold_reached(hass) is True


# --- SmartPI's learning ---------------------------------------------------------


def _smartpi() -> SmartPI:
    smartpi = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Boiler"
    )
    force_smartpi_stable_mode(smartpi)
    smartpi._committed_on_percent = 0.6
    return smartpi


def _calculate_twice(smartpi: SmartPI, monkeypatch) -> None:
    """Two calculations ten minutes apart, so that learning is fed."""
    now = [5000.0]
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: now[0])
    for _ in range(2):
        smartpi.calculate(
            target_temp=20.0, current_temp=19.0, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT
        )
        now[0] += 600.0


def test_pause_drops_the_observations_in_flight() -> None:
    smartpi = _smartpi()
    smartpi.learn_win._active = True
    smartpi._t_heat_episode_start = 100.0

    smartpi.set_boiler_learning_paused(True)

    assert smartpi.boiler_learning_paused is True
    assert smartpi.learn_win_active is False
    assert smartpi._t_heat_episode_start is None
    assert smartpi.dt_est.last_power == pytest.approx(0.6)


def test_no_learning_during_the_pause(monkeypatch) -> None:
    """Neither the room model nor the dead time is learned."""
    smartpi = _smartpi()
    smartpi.set_boiler_learning_paused(True)
    smartpi.learn_win.update = MagicMock(return_value=(0, 0))
    smartpi.dt_est.update = MagicMock()

    _calculate_twice(smartpi, monkeypatch)

    smartpi.learn_win.update.assert_not_called()
    smartpi.dt_est.update.assert_not_called()


@pytest.mark.parametrize("paused", [True, False])
def test_dead_time_before_the_model_is_learned(paused, monkeypatch) -> None:
    """Before the model is learned, SmartPI regulates by hysteresis and still measures the dead time."""
    smartpi = SmartPI(
        hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Boiler"
    )
    smartpi.set_boiler_learning_paused(paused)
    smartpi.dt_est.update = MagicMock()

    _calculate_twice(smartpi, monkeypatch)

    assert smartpi.dt_est.update.called is not paused


@pytest.mark.parametrize("paused", [False, True])
def test_the_estimator_learns_nothing_during_the_pause(paused, monkeypatch) -> None:
    """Watches the estimator itself, so that any path to it is covered.

    Eight hours of a room cooling by 0.18 °C per hour: without the pause the
    data reach the estimator; with the pause they never do, and a and b stay.
    """
    smartpi = _smartpi()
    smartpi._committed_on_percent = 0.0
    for channel in ("heat", "cool"):
        setattr(smartpi.dt_est, f"deadtime_{channel}_s", 300.0)
        setattr(smartpi.dt_est, f"deadtime_{channel}_reliable", True)
    smartpi.set_boiler_learning_paused(paused)
    learn_calls = []
    learn = ABEstimator.learn

    def spy(estimator, *args, **kwargs):
        learn_calls.append(1)
        return learn(estimator, *args, **kwargs)

    monkeypatch.setattr(ABEstimator, "learn", spy)
    now = [10000.0]
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: now[0])
    a_before, b_before = smartpi.est.a, smartpi.est.b
    temperature = 19.0
    for _ in range(8 * 60):
        now[0] += 60.0
        temperature -= 0.003
        smartpi.calculate(
            target_temp=21.0,
            current_temp=round(temperature, 1),
            ext_current_temp=5.0,
            slope=-0.18,
            hvac_mode=VThermHvacMode_HEAT,
        )
        smartpi._committed_on_percent = 0.0

    if paused:
        assert learn_calls == []
        assert (smartpi.est.a, smartpi.est.b) == (a_before, b_before)
    else:
        assert learn_calls


@pytest.mark.parametrize("pause", [None, "boiler", "user"])
def test_calibration_measures_the_dead_time_only_while_learning_runs(pause, monkeypatch) -> None:
    """A calibration's heating step does not feed the dead-time estimator during a pause, whatever its cause."""
    smartpi = _smartpi()
    smartpi.force_calibration()
    now = [1000.0]
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: now[0])
    smartpi.calculate(target_temp=18.0, current_temp=17.9, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT)
    smartpi.calibration_mgr._calibration_state = SmartPICalibrationPhase.HEAT_UP
    smartpi._committed_on_percent = 1.0
    if pause == "boiler":
        smartpi.set_boiler_learning_paused(True)
    elif pause == "user":
        smartpi.set_learning_enabled(False)
    smartpi.dt_est.update = MagicMock()

    now[0] += 60.0
    smartpi.calculate(target_temp=18.0, current_temp=17.9, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT)

    assert smartpi.calibration_state == SmartPICalibrationPhase.HEAT_UP
    assert smartpi.on_percent == pytest.approx(1.0)
    assert smartpi.dt_est.update.called is (pause is None)


def test_diagnostics_show_the_pause() -> None:
    smartpi = _smartpi()

    assert build_published_diagnostics(smartpi)["learning"]["boiler_paused"] is False

    smartpi.set_boiler_learning_paused(True)

    learning = build_published_diagnostics(smartpi)["learning"]
    assert learning["boiler_paused"] is True
    assert learning["enabled"] is True


def test_learning_runs_without_the_pause(monkeypatch) -> None:
    smartpi = _smartpi()
    smartpi.learn_win.update = MagicMock(return_value=(0, 0))
    smartpi.dt_est.update = MagicMock()

    _calculate_twice(smartpi, monkeypatch)

    smartpi.learn_win.update.assert_called()
    smartpi.dt_est.update.assert_called()


def test_the_user_setting_is_untouched() -> None:
    """The user's pause and the boiler's pause are two separate causes."""
    smartpi = _smartpi()
    smartpi.set_learning_enabled(False)

    smartpi.set_boiler_learning_paused(True)
    smartpi.set_boiler_learning_paused(False)

    assert smartpi.learning_enabled is False
    assert smartpi._learning_active is False
    assert smartpi.save_state()["learning_enabled"] is False

    smartpi.set_learning_enabled(True)

    assert smartpi._learning_active is True


def test_after_the_pause_learning_waits_for_the_room_dead_time(monkeypatch) -> None:
    """As after any start of heating, SmartPI does not learn during the room's dead time."""
    smartpi = _smartpi()
    smartpi.dt_est.deadtime_heat_s = 1200.0
    smartpi.dt_est.deadtime_heat_reliable = True
    smartpi._t_heat_episode_start = 100.0
    smartpi.set_boiler_learning_paused(True)
    smartpi._update_deadtime_episode_status(0.6, VThermHvacMode_HEAT, 200.0)

    smartpi.set_boiler_learning_paused(False)
    smartpi._update_deadtime_episode_status(0.6, VThermHvacMode_HEAT, 5000.0)

    assert smartpi._t_heat_episode_start == 5000.0
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: 5600.0)
    assert smartpi.in_deadtime_window is True
    smartpi.update_learning(dt_min=10.0, current_temp=19.0, ext_temp=5.0, u_active=0.6, hvac_mode=VThermHvacMode_HEAT)
    assert smartpi.est.learn_last_reason == "skip: deadtime window"

    monkeypatch.setattr(algo_module.time, "monotonic", lambda: 6300.0)
    assert smartpi.in_deadtime_window is False


def test_regulation_is_unchanged(monkeypatch) -> None:
    """SmartPI still sees the room cool and keeps calling for heat."""
    paused, normal = _smartpi(), _smartpi()
    paused.set_boiler_learning_paused(True)

    _calculate_twice(paused, monkeypatch)
    _calculate_twice(normal, monkeypatch)

    assert paused.on_percent == pytest.approx(normal.on_percent)
    assert paused.on_percent > 0.0
    assert paused.ctl.integral == pytest.approx(normal.ctl.integral)


# --- the handler ----------------------------------------------------------------


def _handler(counted: bool = True) -> SmartPIHandler:
    thermostat = FakeThermostatRuntime()
    thermostat.is_used_by_central_boiler = counted
    handler = SmartPIHandler(thermostat)
    with patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"):
        handler.init_algorithm()
    return handler


async def _control(handler: SmartPIHandler, reached: bool | None, power: float) -> SmartPI:
    """One calculation; returns the algorithm, with the diagnostics' publications in ``handler.published``."""
    algo = handler._thermostat.prop_algorithm
    algo._committed_on_percent = power
    with (
        patch("custom_components.vtherm_smartpi.handler.central_boiler_threshold_reached", return_value=reached),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send") as send,
        patch.object(handler, "_async_save", new_callable=AsyncMock),
    ):
        await handler.control_heating(force=False)
    handler.published = send.call_count
    handler.should_publish_intermediate()  # as Versatile Thermostat asks after each calculation
    return algo


async def test_handler_pauses_learning_below_the_threshold_at_two_calculations() -> None:
    """The first calculation below the threshold does not pause: at a boiler's start Versatile
    Thermostat counts the new call a moment later. The second one does."""
    handler = _handler()

    algo = await _control(handler, reached=False, power=0.5)
    assert algo.boiler_learning_paused is False

    algo = await _control(handler, reached=False, power=0.5)
    assert algo.boiler_learning_paused is True


async def test_no_pause_when_the_boiler_starts() -> None:
    """Below the threshold at one calculation only, as when the boiler starts: no pause."""
    handler = _handler()

    await _control(handler, reached=False, power=0.5)
    algo = await _control(handler, reached=True, power=0.5)
    assert algo.boiler_learning_paused is False

    algo = await _control(handler, reached=False, power=0.5)
    assert algo.boiler_learning_paused is False


@pytest.mark.parametrize(
    ("counted", "reached", "power"),
    [
        (False, False, 0.5),  # Versatile Thermostat does not count it for the boiler
        (True, False, 0.0),  # SmartPI does not call for heat
        (True, True, 0.5),  # the threshold is reached
        (True, None, 0.5),  # the threshold cannot be read
    ],
)
async def test_handler_does_not_pause(counted, reached, power) -> None:
    handler = _handler(counted)
    await _control(handler, reached=reached, power=power)
    algo = await _control(handler, reached=reached, power=power)

    assert algo.boiler_learning_paused is False


async def test_handler_resumes_learning_at_once() -> None:
    """Learning resumes at the first calculation that reaches the threshold, or does not call."""
    for reached, power in ((True, 0.5), (False, 0.0)):
        handler = _handler()
        await _control(handler, reached=False, power=0.5)
        await _control(handler, reached=False, power=0.5)

        algo = await _control(handler, reached=reached, power=power)

        assert algo.boiler_learning_paused is False


async def test_a_change_of_the_pause_is_published_at_once() -> None:
    """The diagnostics are published when the pause changes, though no input changed. SmartPI
    past its first phase: in hysteresis every calculation commits a new command, which publishes."""
    handler = _handler()
    force_smartpi_stable_mode(handler._thermostat.prop_algorithm)
    await _control(handler, reached=False, power=0.5)
    await _control(handler, reached=False, power=0.5)
    assert handler._thermostat.prop_algorithm.boiler_learning_paused is True
    assert handler.published >= 1

    await _control(handler, reached=False, power=0.5)
    assert handler.published == 0

    await _control(handler, reached=True, power=0.5)
    assert handler.published >= 1


def test_the_diagnostics_publish_when_the_heating_episode_started() -> None:
    """``analysis.deadtime.heat_episode_start``: the heating episode's start in UTC; the pause ends
    the episode, and the first heating after the resume starts a new one, later than the resume."""
    import time as clock
    from datetime import datetime, timedelta

    smartpi = _smartpi()
    smartpi._t_heat_episode_start = clock.monotonic() - 600.0

    def started() -> datetime | None:
        value = build_published_diagnostics(smartpi)["analysis"]["deadtime"]["heat_episode_start"]
        return datetime.fromisoformat(value) if value is not None else None

    before = started()
    assert before is not None and before.utcoffset().total_seconds() == 0
    assert abs((datetime.now(before.tzinfo) - before).total_seconds() - 600.0) < 5.0

    smartpi.set_boiler_learning_paused(True)
    assert started() is None
    smartpi._update_deadtime_episode_status(0.6, VThermHvacMode_HEAT, clock.monotonic())
    during = started()
    smartpi.set_boiler_learning_paused(False)
    assert started() is None
    resumed = datetime.now(before.tzinfo)
    smartpi._update_deadtime_episode_status(0.6, VThermHvacMode_HEAT, clock.monotonic())

    # The wall and monotonic clocks are read a few microseconds apart.
    assert started() >= resumed - timedelta(milliseconds=50)
    assert resumed > during
    assert build_published_diagnostics(smartpi)["analysis"]["deadtime"]["cool_episode_start"] is None
