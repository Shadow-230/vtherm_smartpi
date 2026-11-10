"""SmartPI does not learn heating while it calls for heat and Versatile Thermostat's central boiler
is off (documentation/en/technical_doc.md, 3.8)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

import custom_components.vtherm_smartpi.algo as algo_module
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.boiler_call import (
    BOILER_PAUSE_CONFIRM_S,
    VT_CENTRAL_BOILER_UNIQUE_ID,
    VT_DOMAIN,
    central_boiler_on,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.const import SMARTPI_RECALC_INTERVAL_SEC, SmartPICalibrationPhase
from custom_components.vtherm_smartpi.smartpi.deadtime_estimator import DeadTimeEstimator
from custom_components.vtherm_smartpi.smartpi.diagnostics import build_published_diagnostics
from custom_components.vtherm_smartpi.smartpi.tint_filter import AdaptiveTintFilter
from fakes.fake_thermostat_runtime import FakeThermostatRuntime
from helpers import force_smartpi_stable_mode

RECALC_S = float(SMARTPI_RECALC_INTERVAL_SEC)


# --- reading Versatile Thermostat's central boiler -------------------------------


async def test_the_boiler_is_unknown_without_its_entity(hass) -> None:
    assert central_boiler_on(hass) is None


@pytest.mark.parametrize(("state", "on"), [("on", True), ("off", False), ("unavailable", None), ("unknown", None)])
async def test_the_boiler_s_state(hass, state, on) -> None:
    entry = er.async_get(hass).async_get_or_create("binary_sensor", VT_DOMAIN, VT_CENTRAL_BOILER_UNIQUE_ID, suggested_object_id="central_boiler")
    hass.states.async_set(entry.entity_id, state)

    assert central_boiler_on(hass) is on


# --- SmartPI's learning ------------------------------------------------------------


def _smartpi() -> SmartPI:
    smartpi = SmartPI(hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Boiler")
    force_smartpi_stable_mode(smartpi)
    smartpi._committed_on_percent = 0.6
    return smartpi


def _calculate_twice(smartpi: SmartPI, monkeypatch) -> None:
    """Two calculations ten minutes apart, so that learning is fed."""
    now = [5000.0]
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: now[0])
    for _ in range(2):
        smartpi.calculate(target_temp=20.0, current_temp=19.0, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT)
        now[0] += 600.0


@pytest.mark.parametrize("paused", [False, True])
def test_no_learning_during_the_pause(paused, monkeypatch) -> None:
    """Neither the room model nor the dead time is learned during the pause; without it both are."""
    smartpi = _smartpi()
    smartpi.set_boiler_learning_paused(paused)
    smartpi.learn_win.update = MagicMock(return_value=(0, 0))
    smartpi.dt_est.update = MagicMock()

    _calculate_twice(smartpi, monkeypatch)

    assert smartpi.learn_win.update.called is not paused
    assert smartpi.dt_est.update.called is not paused


def test_the_pause_drops_the_observations_in_flight(monkeypatch) -> None:
    """Learning's window and dead-time detection are dropped, and learning's episode starts at the
    change; the regulation's episode keeps its start."""
    smartpi = _smartpi()
    smartpi.learn_win._active = True
    smartpi._t_heat_episode_start = 100.0
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: 500.0)

    smartpi.set_boiler_learning_paused(True)

    assert smartpi.boiler_learning_paused is True
    assert smartpi.learn_win_active is False
    assert smartpi.dt_est.last_power == pytest.approx(0.6)
    assert smartpi._t_heat_episode_start == 100.0
    assert smartpi._learning_episode_start(smartpi._t_heat_episode_start) == 500.0


def test_after_the_pause_learning_waits_for_the_room_dead_time(monkeypatch) -> None:
    """After the resume SmartPI does not learn during the room's dead time; the regulation's
    dead-time window still counts from the heating's own start."""
    smartpi = _smartpi()
    smartpi.dt_est.deadtime_heat_s = 1200.0
    smartpi.dt_est.deadtime_heat_reliable = True
    smartpi._t_heat_episode_start = 100.0
    now = [200.0]
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: now[0])
    smartpi.set_boiler_learning_paused(True)
    now[0] = 5000.0
    smartpi.set_boiler_learning_paused(False)

    now[0] = 5600.0
    assert smartpi.in_deadtime_window is False  # the regulation's window ended at 1300 s
    smartpi.update_learning(dt_min=10.0, current_temp=19.0, ext_temp=5.0, u_active=0.6, hvac_mode=VThermHvacMode_HEAT)
    assert smartpi.est.learn_last_reason == "skip: deadtime window"

    now[0] = 6300.0
    smartpi.update_learning(dt_min=10.0, current_temp=19.0, ext_temp=5.0, u_active=0.6, hvac_mode=VThermHvacMode_HEAT)
    assert smartpi.est.learn_last_reason != "skip: deadtime window"


def test_a_pause_and_its_resume_leave_the_regulation_as_without_them(monkeypatch) -> None:
    """The same inputs, once with a pause and its resume and once without: the command, the
    integral and the regulation's dead-time window (in which the controller holds its integral)
    are the same at every calculation. The valve applies each command at once. The user's
    learning setting is off in both, so that learning changes no model either way."""
    runs = []
    for pause_at, resume_at in ((3, 10), (None, None)):
        smartpi = _smartpi()
        smartpi.set_learning_enabled(False)
        smartpi.dt_est.deadtime_heat_s = 780.0
        smartpi.dt_est.deadtime_heat_reliable = True
        now = [10_000.0]
        monkeypatch.setattr(algo_module.time, "monotonic", lambda now=now: now[0])
        steps = []
        for i in range(30):
            if i == pause_at:
                smartpi.set_boiler_learning_paused(True)
            if i == resume_at:
                smartpi.set_boiler_learning_paused(False)
            smartpi.calculate(target_temp=20.0, current_temp=19.6 + 0.01 * i, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT)
            smartpi.on_applied_power_updated(smartpi.on_percent, VThermHvacMode_HEAT)
            steps.append((smartpi.on_percent, smartpi.ctl.integral, smartpi.in_deadtime_window))
            now[0] += RECALC_S
        runs.append(steps)

    paused, normal = runs
    for i, (a, b) in enumerate(zip(paused, normal, strict=True)):
        assert a[0] == pytest.approx(b[0]) and a[1] == pytest.approx(b[1]) and a[2] == b[2], (i, a, b)
    assert {window for _, _, window in normal} == {True, False}  # the window opened and closed
    assert len({round(integral, 6) for _, integral, _ in normal}) > 1  # the integral moved


@pytest.mark.parametrize("pause", [None, "boiler", "user"])
def test_a_calibration_does_not_time_the_dead_time_with_the_boiler_off(pause, monkeypatch) -> None:
    """A calibration's heating step does not feed the dead-time estimator during the boiler pause;
    during the user's pause it does, as in the author's code."""
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
    assert smartpi.dt_est.update.called is (pause != "boiler")


def _paused_while_calling(monkeypatch, now: list[float], learning: bool = True) -> SmartPI:
    """SmartPI calling at full power with a filtered room temperature, paused at 100 s."""
    smartpi = _smartpi()
    smartpi.set_learning_enabled(learning)
    smartpi._committed_on_percent = 1.0
    smartpi.tint_filter.update(19.0, 0.0)
    monkeypatch.setattr(algo_module.time, "monotonic", lambda: now[0])
    now[0] = 100.0
    smartpi.set_boiler_learning_paused(True)
    return smartpi


def test_the_boiler_s_start_arms_the_dead_time_detection_from_then(monkeypatch) -> None:
    """The pause ends because the boiler starts while SmartPI calls: the detection arms as at a
    start from 0, its time counted from the boiler's start, not from the call that waited."""
    now = [0.0]
    smartpi = _paused_while_calling(monkeypatch, now)
    assert smartpi.dt_est.state == "OFF"

    now[0] = 220.0
    smartpi.set_boiler_learning_paused(False, boiler_started=True)

    assert smartpi.dt_est.state == "WAITING_HEAT_RESPONSE"
    assert smartpi.dt_est.heat_start_time == 220.0


@pytest.mark.parametrize(("boiler_started", "learning", "power"), [(False, True, 1.0), (True, False, 1.0), (True, True, 0.5)])
def test_no_detection_without_the_boiler_s_start(boiler_started, learning, power, monkeypatch) -> None:
    """No detection arms when the pause ends for another reason (SmartPI stopped calling), while
    the user's learning is off, or at a power the estimator does not time (its own 80 % rule)."""
    now = [0.0]
    smartpi = _paused_while_calling(monkeypatch, now, learning=learning)
    smartpi._committed_on_percent = power

    now[0] = 220.0
    smartpi.set_boiler_learning_paused(False, boiler_started=boiler_started)

    assert smartpi.dt_est.state != "WAITING_HEAT_RESPONSE"


def test_the_user_setting_is_untouched() -> None:
    """The user's pause and the boiler's pause are two separate causes."""
    smartpi = _smartpi()
    smartpi.set_learning_enabled(False)
    smartpi.set_boiler_learning_paused(True)
    smartpi.set_boiler_learning_paused(False)

    assert smartpi.learning_enabled is False
    assert smartpi._learning_active is False
    assert smartpi.save_state()["learning_enabled"] is False


def test_the_diagnostics_show_the_pause() -> None:
    smartpi = _smartpi()
    assert build_published_diagnostics(smartpi)["learning"]["boiler_paused"] is False

    smartpi.set_boiler_learning_paused(True)

    learning = build_published_diagnostics(smartpi)["learning"]
    assert (learning["boiler_paused"], learning["enabled"]) == (True, True)


def _heat_then_stop_then_start(est, *, pre_c_per_h: float, off_min: float, pause=None) -> None:
    """A room heated for an hour (fed to ``est`` until ``pause(True)``, as SmartPI feeds no
    dead-time detection during the pause), stopped at 0 s (``pause(False)``: the pause ends as the
    room stops calling), free-running for ``off_min`` with a trend of ``pre_c_per_h``, then
    started at full power: its response comes 300 s later at 3 degC/h. Fed every 60 s, read to
    0.01 degC, through the regulation's filter, until the detection ends."""
    filt = AdaptiveTintFilter("test")
    start = off_min * 60.0
    now = -3600.0
    while now <= start + 7200.0:
        if pause is not None and now in (-1800.0, 0.0):
            pause(now < 0.0)
        if now < 0.0:
            power, temp = 1.0, 20.0 + now / 3600.0
        else:
            power = 1.0 if now >= start else 0.0
            temp = 20.0 + pre_c_per_h * now / 3600.0 + 3.0 * max(0.0, now - start - 300.0) / 3600.0
        raw = round(temp, 2)
        lp, _ = filt.update(raw, 10_000.0 + now)
        if pause is None or not -1800.0 <= now < 0.0:
            est.update(10_000.0 + now, lp, power, hvac_mode=VThermHvacMode_HEAT, tin_raw=raw)
        if now >= start and est.state != "WAITING_HEAT_RESPONSE":
            return
        now += 60.0


@pytest.mark.parametrize(("pre_c_per_h", "off_min"), [(0.0, 60.0), (0.2, 60.0), (-0.3, 60.0), (0.2, 10.0)])
def test_a_pause_leaves_the_next_start_s_dead_time_as_without_it(pre_c_per_h, off_min) -> None:
    """The dead time is measured against the room's trend before the start, from the period
    without heating. A change of the pause clears the detection's temperature history, as the
    user's pause does; it happens only while SmartPI calls or as it stops, so the next start's
    outcome, dead time and trend are those of the same room without the pause."""
    paused = _smartpi()

    def pause(on: bool) -> None:
        if not on:
            paused._committed_on_percent = 0.0  # the room has stopped calling
        paused.set_boiler_learning_paused(on)

    _heat_then_stop_then_start(paused.dt_est, pre_c_per_h=pre_c_per_h, off_min=off_min, pause=pause)
    normal = DeadTimeEstimator()
    _heat_then_stop_then_start(normal, pre_c_per_h=pre_c_per_h, off_min=off_min)

    assert paused.dt_est.last_outcome["heat"] == normal.last_outcome["heat"] == "detected"
    assert paused.dt_est._history_heat == normal._history_heat
    assert paused.dt_est._trend_slope == pytest.approx(normal._trend_slope)


# --- the handler -------------------------------------------------------------------


def _handler(counted: bool = True) -> SmartPIHandler:
    thermostat = FakeThermostatRuntime()
    thermostat.is_used_by_central_boiler = counted
    handler = SmartPIHandler(thermostat)
    with patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"):
        handler.init_algorithm()
    return handler


async def _control(handler: SmartPIHandler, boiler_on: bool | None, power: float, at: float | None = None) -> SmartPI:
    """One calculation at ``at`` (the handler's monotonic time; by default one calculation period
    after the previous one); returns the algorithm, with the diagnostics' publications in
    ``handler.published``."""
    algo = handler._thermostat.prop_algorithm
    algo._committed_on_percent = power
    handler.clock = at if at is not None else getattr(handler, "clock", 1000.0 - RECALC_S) + RECALC_S
    with (
        patch("custom_components.vtherm_smartpi.handler.time.monotonic", return_value=handler.clock),
        patch("custom_components.vtherm_smartpi.handler.central_boiler_on", return_value=boiler_on),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send") as send,
        patch.object(handler, "_async_save", new_callable=AsyncMock),
    ):
        await handler.control_heating(force=False)
    handler.published = send.call_count
    handler.should_publish_intermediate()  # as Versatile Thermostat asks after each calculation
    return algo


async def test_a_calculation_period_with_the_boiler_off_pauses() -> None:
    """The pause starts at the first calculation after one period (60 s, less 1 s for the timer)
    of calling with the boiler off, not before; it resumes at once when the boiler is on."""
    handler = _handler()

    await _control(handler, boiler_on=False, power=0.5, at=1000.0)
    algo = await _control(handler, boiler_on=False, power=0.5, at=1000.0 + BOILER_PAUSE_CONFIRM_S - 0.1)
    assert algo.boiler_learning_paused is False
    algo = await _control(handler, boiler_on=False, power=0.5, at=1000.0 + BOILER_PAUSE_CONFIRM_S)
    assert algo.boiler_learning_paused is True
    assert BOILER_PAUSE_CONFIRM_S == RECALC_S - 1.0

    algo = await _control(handler, boiler_on=True, power=0.5, at=1100.0)
    assert algo.boiler_learning_paused is False


async def test_an_own_start_that_turns_the_boiler_on_within_seconds_does_not_pause() -> None:
    """A room's own start: the setpoint's calculation, a new room temperature and the periodic
    calculation while its valve has not reported (15 s), then Versatile Thermostat counts it and
    turns the boiler on."""
    handler = _handler()
    algo = handler._thermostat.prop_algorithm
    algo.set_boiler_learning_paused = MagicMock(wraps=algo.set_boiler_learning_paused)

    for at, on in ((1000.0, False), (1000.002, False), (1010.0, False), (1015.2, True), (1060.0, True)):
        algo = await _control(handler, boiler_on=on, power=1.0, at=at)
        assert algo.boiler_learning_paused is False
    algo.set_boiler_learning_paused.assert_not_called()  # the detection armed at the start runs on


async def test_the_pause_holds_while_the_boiler_waits_for_its_activation_delay() -> None:
    """The threshold is reached but Versatile Thermostat starts the boiler 120 s later: the room
    gets no heat meanwhile, so learning pauses after a period and resumes when the boiler is on."""
    handler = _handler()

    paused = [(await _control(handler, boiler_on=t >= 1120.0, power=1.0, at=t)).boiler_learning_paused for t in (1000.0, 1040.0, 1100.0, 1120.0)]

    assert paused == [False, False, True, False]


@pytest.mark.parametrize(("boiler_on", "power"), [(True, 0.5), (False, 0.0), (None, 0.5)])
async def test_a_break_restarts_the_time(boiler_on, power) -> None:
    """The boiler on, no call, or a boiler state that cannot be read breaks the run: the time
    counts again from the next calculation with the boiler off."""
    handler = _handler()

    await _control(handler, boiler_on=False, power=0.5, at=1000.0)
    await _control(handler, boiler_on=boiler_on, power=power, at=1030.0)
    await _control(handler, boiler_on=False, power=0.5, at=1060.0)
    algo = await _control(handler, boiler_on=False, power=0.5, at=1060.0 + BOILER_PAUSE_CONFIRM_S - 0.1)
    assert algo.boiler_learning_paused is False
    algo = await _control(handler, boiler_on=False, power=0.5, at=1060.0 + BOILER_PAUSE_CONFIRM_S)
    assert algo.boiler_learning_paused is True


async def test_no_pause_for_a_thermostat_the_boiler_does_not_heat() -> None:
    handler = _handler(counted=False)
    for at in (1000.0, 1100.0, 1200.0):
        algo = await _control(handler, boiler_on=False, power=0.5, at=at)
    assert algo.boiler_learning_paused is False


async def test_a_change_of_the_pause_is_published_at_once() -> None:
    """The diagnostics are published when the pause changes, though no input changed (SmartPI past
    its first phase, where not every calculation publishes)."""
    handler = _handler()
    force_smartpi_stable_mode(handler._thermostat.prop_algorithm)
    await _control(handler, boiler_on=False, power=0.5)
    await _control(handler, boiler_on=False, power=0.5)
    assert handler._thermostat.prop_algorithm.boiler_learning_paused is True
    assert handler.published >= 1

    await _control(handler, boiler_on=False, power=0.5)
    assert handler.published == 0

    await _control(handler, boiler_on=True, power=0.5)
    assert handler.published >= 1


async def test_the_boiler_s_start_resumes_at_once_and_arms_the_detection() -> None:
    """Versatile Thermostat's boiler starts while the room waits for it, paused: the change of
    the boiler's state resumes learning at that moment, without a calculation, publishes it, and
    arms the dead-time detection from then."""
    handler = _handler()
    await _control(handler, boiler_on=False, power=1.0, at=1000.0)
    algo = await _control(handler, boiler_on=False, power=1.0, at=1000.0 + BOILER_PAUSE_CONFIRM_S)
    assert algo.boiler_learning_paused is True
    algo.calculate = MagicMock()

    with (
        patch("custom_components.vtherm_smartpi.handler.time.monotonic", return_value=1120.0),
        patch("custom_components.vtherm_smartpi.algo.time.monotonic", return_value=1120.0),
        patch("custom_components.vtherm_smartpi.handler.central_boiler_on", return_value=True),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send") as send,
    ):
        handler._on_boiler_change(MagicMock())

    assert algo.boiler_learning_paused is False
    assert send.called
    algo.calculate.assert_not_called()
    assert (algo.dt_est.state, algo.dt_est.heat_start_time) == ("WAITING_HEAT_RESPONSE", 1120.0)


async def test_a_room_that_stops_before_the_boiler_starts_resumes_without_a_detection() -> None:
    """The pause ends because SmartPI stops calling (its calculation not run here, which would
    start the test thermostat calling again)."""
    handler = _handler()
    await _control(handler, boiler_on=False, power=1.0, at=1000.0)
    algo = await _control(handler, boiler_on=False, power=1.0, at=1000.0 + BOILER_PAUSE_CONFIRM_S)
    algo.calculate = MagicMock()

    algo = await _control(handler, boiler_on=False, power=0.0, at=1080.0)

    assert algo.boiler_learning_paused is False
    assert algo.dt_est.state != "WAITING_HEAT_RESPONSE"


def test_the_handler_follows_the_boiler_once_its_entity_exists_and_stops_on_removal() -> None:
    handler = _handler()
    unsubscribe = MagicMock()
    with (
        patch("custom_components.vtherm_smartpi.handler.central_boiler_entity_id", side_effect=[None, "binary_sensor.central_boiler"]),
        patch("custom_components.vtherm_smartpi.handler.async_track_state_change_event", return_value=unsubscribe) as track,
    ):
        handler._listen_to_the_boiler()
        assert track.call_count == 0
        handler._listen_to_the_boiler()
        handler._listen_to_the_boiler()
    assert track.call_count == 1
    assert track.call_args.args[1] == ["binary_sensor.central_boiler"]

    handler._store = None  # no state to save here
    with patch("custom_components.vtherm_smartpi.handler.clear_valve_config_issues"):
        handler.remove()
    unsubscribe.assert_called_once()
