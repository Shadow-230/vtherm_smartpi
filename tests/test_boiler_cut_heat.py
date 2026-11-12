"""The central boiler stops heating a room that still calls (documentation/en/technical_doc.md,
3.8): the dead-time estimator sees the power fall to 0 at that moment, and no power until the boiler
heats the room again, so the author's rules time the passive response from the boiler's stop and
the room's own stop changes nothing. Built on the bench's probe of 2026-11-11: the real handler and
SmartPI's real calculation, times on the handler's monotonic clock. The room heated from 0 s (dead
time 780 s, reliable); the boiler goes off at 1030 s; the room's passive response starts 780 s
after its heat really ended, then it cools 0.3 degC/h."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.handler import SmartPIHandler
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT, VThermHvacMode_OFF
from fakes.fake_thermostat_runtime import FakeThermostatRuntime
from helpers import force_smartpi_stable_mode

STOP = 1030.0  # the boiler goes off (or, in the reference, the room stops) here
DEAD = 780.0
COOL_C_PER_S = 0.3 / 3600.0
T0 = 19.0
REFERENCE_S = 1020.0  # the passive dead time of the reference (the bench's probe)


def _handler(heat_start: float = 0.0) -> SmartPIHandler:
    thermostat = FakeThermostatRuntime()
    thermostat.is_used_by_central_boiler = True
    handler = SmartPIHandler(thermostat)
    with patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"):
        handler.init_algorithm()
    algo = thermostat.prop_algorithm
    force_smartpi_stable_mode(algo)
    est = algo.dt_est
    est.deadtime_heat_s, est.deadtime_heat_reliable = DEAD, True
    est.deadtime_cool_s, est.deadtime_cool_reliable = DEAD, True
    est.last_start_time, est.last_power, est.state = heat_start, 1.0, "HEATING"
    return handler


def _temp(at: float) -> float:
    """Flat until the passive response arrives, DEAD after the heat really ended, then cooling."""
    return T0 - max(0.0, at - STOP - DEAD) * COOL_C_PER_S


async def _step(handler: SmartPIHandler, at: float, boiler_on: bool, committed: float) -> SmartPI:
    """One control at ``at``: SmartPI's committed power, the pause check, the real calculation."""
    t = handler._thermostat
    algo = t.prop_algorithm
    algo._committed_on_percent = committed
    t.current_temperature = _temp(at)
    with (
        patch("custom_components.vtherm_smartpi.handler.time.monotonic", return_value=at),
        patch("custom_components.vtherm_smartpi.algo.time.monotonic", return_value=at),
        patch("custom_components.vtherm_smartpi.handler.central_boiler_on", return_value=boiler_on),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"),
        patch.object(handler, "_async_save", new_callable=AsyncMock),
    ):
        await handler.control_heating(force=False)
    return algo


def _boiler(handler: SmartPIHandler, on: bool, at: float) -> None:
    handler._thermostat.current_temperature = _temp(at)
    with (
        patch("custom_components.vtherm_smartpi.handler.time.monotonic", return_value=at),
        patch("custom_components.vtherm_smartpi.algo.time.monotonic", return_value=at),
        patch("custom_components.vtherm_smartpi.handler.central_boiler_on", return_value=on),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"),
    ):
        handler._on_boiler_change(MagicMock())


async def _heated(heat_start: float = 0.0) -> SmartPIHandler:
    """The room heated with the boiler on, a calculation a minute, up to 1000 s."""
    handler = _handler(heat_start)
    for at in range(400, 1001, 60):
        await _step(handler, float(at), True, 1.0)
    return handler


async def _cools_down(handler: SmartPIHandler, after: float, boiler_on: bool) -> None:
    """The room not calling, a calculation a minute, until the passive detection ends."""
    est = handler._thermostat.prop_algorithm.dt_est
    at = after
    while at < after + 2400.0 and est.state == "WAITING_COOL_RESPONSE":
        at += 60.0
        await _step(handler, at, boiler_on, 0.0)


async def _reference(times: list[float]) -> list[float]:
    """The room stops itself at STOP with the boiler on, then SmartPI calculates at ``times`` and
    each minute until the passive detection ends: the author's measurement, on the same samples."""
    handler = await _heated()
    algo = await _step(handler, STOP, True, 0.0)
    est = algo.dt_est
    assert (est.state, est.cool_start_time, est.last_stop_time) == ("WAITING_COOL_RESPONSE", STOP, STOP)
    for at in times:
        await _step(handler, at, True, 0.0)
    await _cools_down(handler, times[-1] if times else STOP, True)
    assert est.last_outcome["cool"] == "detected"
    return list(est._history_cool)


async def test_the_reference_stop_with_the_boiler_on_is_the_author_s() -> None:
    """Without the boiler's cut the room's own stop arms the passive response from the stop (the
    bench's probe: 1020 s)."""
    assert await _reference([]) == [REFERENCE_S]


@pytest.mark.parametrize(
    "calculations",
    [
        [(1060.0, 0.0)],  # the room's stop seen 30 s after the boiler's
        [(1088.0, 0.0)],  # 58 s after: before the pause
        [(1060.0, 1.0), (1120.0, 0.0)],  # a calculation while it calls, its stop committed between
        [(1090.0, 1.0), (1150.0, 0.0)],  # paused first, then it stops
    ],
    ids=["30 s", "58 s", "90 s", "paused"],
)
async def test_the_boiler_s_cut_times_the_passive_response_from_its_stop(calculations) -> None:
    """The boiler goes off at 1030 s after a settled heating: the passive response is timed from
    1030 s, whenever the room's own stop comes after it and whether learning paused meanwhile; the
    room's stop changes nothing, and the dead time is the reference's on the same samples."""
    handler = await _heated()
    algo = handler._thermostat.prop_algorithm
    est = algo.dt_est

    _boiler(handler, False, STOP)
    assert (est.state, est.cool_start_time, est.last_stop_time, est.last_power) == ("WAITING_COOL_RESPONSE", STOP, STOP, 0.0)
    for at, committed in calculations:
        await _step(handler, at, False, committed)
        assert (est.state, est.cool_start_time, est.last_stop_time) == ("WAITING_COOL_RESPONSE", STOP, STOP)

    await _cools_down(handler, calculations[-1][0], False)

    assert est.last_outcome["cool"] == "detected"
    assert list(est._history_cool) == await _reference([at for at, _ in calculations])


async def test_the_boiler_s_cut_after_a_short_heating_records_the_stop_only() -> None:
    """The room heated 430 s, less than its dead time: the boiler's cut records the stop and arms
    nothing, and the room's own stop afterwards arms nothing either."""
    handler = await _heated(heat_start=600.0)
    est = handler._thermostat.prop_algorithm.dt_est

    _boiler(handler, False, STOP)
    await _step(handler, STOP + 60.0, False, 0.0)

    assert est.state != "WAITING_COOL_RESPONSE" and est.last_stop_time == STOP and est.last_outcome["cool"] is None


@pytest.mark.parametrize(("back_after_s", "measured"), [(300.0, False), (700.0, True)])
async def test_the_boiler_s_return_before_the_response_ends_the_passive_detection(back_after_s, measured) -> None:
    """The boiler heats the calling room again before its passive response came: the passive
    detection ends "aborted", whether the start is refused (300 s after the boiler's stop, less
    than 600 s) or measured from the boiler's return (700 s)."""
    handler = await _heated()
    algo = handler._thermostat.prop_algorithm
    est = algo.dt_est
    _boiler(handler, False, STOP)
    await _step(handler, STOP + 60.0, False, 1.0)
    assert algo.boiler_learning_paused and est.state == "WAITING_COOL_RESPONSE"

    back = STOP + back_after_s
    _boiler(handler, True, back)

    assert est.last_outcome["cool"] == "aborted" and est.last_stop_time == STOP
    assert not algo.heat_cut_by_the_boiler
    if measured:
        assert (est.state, est.heat_start_time) == ("WAITING_HEAT_RESPONSE", back)
    else:
        assert est.state == "HEATING"


async def _cut() -> SmartPIHandler:
    handler = await _heated()
    _boiler(handler, False, STOP)
    algo = handler._thermostat.prop_algorithm
    assert algo.heat_cut_by_the_boiler and algo._dead_time_power() == 0.0
    return handler


def _sees_the_command(algo: SmartPI) -> bool:
    return not algo.heat_cut_by_the_boiler and algo._dead_time_power() == algo._committed_on_percent > 0.0


async def test_the_cut_ends_when_the_boiler_heats_the_room_again() -> None:
    """Before learning paused (the boiler back within a calculation period) and at the resume."""
    handler = await _cut()
    _boiler(handler, True, STOP + 20.0)
    assert _sees_the_command(handler._thermostat.prop_algorithm)

    handler = await _cut()
    await _step(handler, STOP + 60.0, False, 1.0)
    _boiler(handler, True, STOP + 120.0)
    assert _sees_the_command(handler._thermostat.prop_algorithm)


async def test_the_cut_ends_with_the_thermostat_off_or_a_change_of_mode() -> None:
    handler = await _cut()
    algo = handler._thermostat.prop_algorithm
    with patch("custom_components.vtherm_smartpi.algo.time.monotonic", return_value=STOP + 60.0):
        algo.calculate(target_temp=20.0, current_temp=19.0, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_OFF)
    algo._committed_on_percent = 1.0
    assert _sees_the_command(algo)
    with patch("custom_components.vtherm_smartpi.algo.time.monotonic", return_value=STOP + 120.0):
        algo.calculate(target_temp=20.0, current_temp=19.0, ext_current_temp=5.0, slope=0.0, hvac_mode=VThermHvacMode_HEAT)
    assert algo.dt_est.last_power > 0.0


async def test_the_cut_ends_with_a_learning_reset_and_a_restart() -> None:
    handler = await _cut()
    algo = handler._thermostat.prop_algorithm
    algo.reset_learning()
    algo._committed_on_percent = 1.0
    assert _sees_the_command(algo)

    handler = await _cut()
    algo = handler._thermostat.prop_algorithm
    saved = algo.save_state()
    algo.load_state(saved)  # a restart restores the saved state; the cut is not saved
    algo._committed_on_percent = 1.0
    assert _sees_the_command(algo)
    restarted = SmartPI(hass=MagicMock(), cycle_min=10, minimal_activation_delay=0, minimal_deactivation_delay=0, name="Restart")
    restarted.load_state(saved)
    restarted._committed_on_percent = 1.0
    assert _sees_the_command(restarted)


async def test_the_cut_ends_as_smartpi_s_command_falls_to_0() -> None:
    """The room stops calling during the cut: the estimator saw no power already, so the stop
    changes nothing, and the cut ends."""
    handler = await _cut()
    algo = await _step(handler, STOP + 30.0, False, 0.0)
    assert not algo.heat_cut_by_the_boiler
    algo._committed_on_percent = 1.0
    assert _sees_the_command(algo)


def _fresh_handler() -> SmartPIHandler:
    thermostat = FakeThermostatRuntime()
    thermostat.is_used_by_central_boiler = True
    handler = SmartPIHandler(thermostat)
    with patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"):
        handler.init_algorithm()
    return handler


async def _control_at(handler: SmartPIHandler, at: float, boiler_on: bool, target: float) -> SmartPI:
    """One control in SmartPI's first phase, where the setpoint's command is committed within
    the calculation (after the handler's check of the pause)."""
    t = handler._thermostat
    t.target_temperature, t.current_temperature = target, 19.0
    with (
        patch("custom_components.vtherm_smartpi.handler.time.monotonic", return_value=at),
        patch("custom_components.vtherm_smartpi.algo.time.monotonic", return_value=at),
        patch("custom_components.vtherm_smartpi.smartpi.deadtime_estimator.time.monotonic", return_value=at),
        patch("custom_components.vtherm_smartpi.handler.central_boiler_on", return_value=boiler_on),
        patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"),
        patch.object(handler, "_async_save", new_callable=AsyncMock),
    ):
        await handler.control_heating(force=False)
    return t.prop_algorithm


@pytest.mark.parametrize("cut", [False, True])
async def test_a_new_call_after_the_cut_s_stop_starts_at_once_in_the_first_phase(cut) -> None:
    """The bench's review (check of c2119c4): a fresh room, cut by the boiler at 1030 s or not,
    stops itself; the boiler runs again for other rooms; the room calls again at 2330 s with the
    boiler on. Its command is committed within that calculation, after the handler's check: the
    heating detection counts from 2330 s either way, not a calculation later."""
    handler = _fresh_handler()
    for at in range(0, 1001, 60):
        await _control_at(handler, float(at), True, 23.0)
    if cut:
        _boiler(handler, False, STOP)
    await _control_at(handler, 1040.0, not cut, 7.0)
    if cut:
        _boiler(handler, True, 1700.0)
    for at in range(1760, 2301, 60):
        await _control_at(handler, float(at), True, 7.0)

    algo = await _control_at(handler, 2330.0, True, 23.0)

    est = algo.dt_est
    assert algo.phase.value == "Hysteresis" and algo.committed_on_percent == 1.0
    assert not algo.heat_cut_by_the_boiler
    assert (est.state, est.last_start_time, est.heat_start_time) == ("WAITING_HEAT_RESPONSE", 2330.0, 2330.0)


@pytest.mark.parametrize("cut", [False, True])
async def test_a_new_call_after_the_cut_s_stop_in_the_stable_phase_is_as_without_the_cut(cut) -> None:
    """In SmartPI's stable phase the command is committed before the handler's check: the new call
    with the boiler on starts at the same moment, cut or not."""
    handler = await _heated()
    if cut:
        _boiler(handler, False, STOP)
    await _step(handler, STOP + 10.0, not cut, 0.0)
    if cut:
        _boiler(handler, True, 1700.0)
    for at in range(1760, 2301, 60):
        await _step(handler, float(at), True, 0.0)

    algo = await _step(handler, 2330.0, True, 1.0)

    est = algo.dt_est
    assert not algo.heat_cut_by_the_boiler
    assert (est.state, est.last_start_time, est.heat_start_time) == ("WAITING_HEAT_RESPONSE", 2330.0, 2330.0)


async def test_a_new_call_with_the_boiler_still_off_waits_for_it_as_before() -> None:
    """After the cut the room stops (the cut ends) and calls again at 1700 s, the boiler still
    off: an ordinary own start that waits for the boiler, as before the cut existed. The heating
    detection arms at the call (670 s without heat since the boiler's stop), the pause drops it,
    and the start counts from the boiler's start at 1900 s."""
    handler = await _heated()
    algo = handler._thermostat.prop_algorithm
    est = algo.dt_est
    _boiler(handler, False, STOP)
    await _step(handler, STOP + 30.0, False, 0.0)
    assert not algo.heat_cut_by_the_boiler

    await _step(handler, 1700.0, False, 1.0)
    assert (est.state, est.heat_start_time) == ("WAITING_HEAT_RESPONSE", 1700.0)
    await _step(handler, 1760.0, False, 1.0)
    assert algo.boiler_learning_paused and (est.state, est.last_outcome["heat"]) == ("HEATING", "aborted")

    _boiler(handler, True, 1900.0)

    assert not algo.boiler_learning_paused
    assert (est.state, est.heat_start_time, est.last_stop_time) == ("WAITING_HEAT_RESPONSE", 1900.0, STOP)
