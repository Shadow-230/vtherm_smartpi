"""A thermostat on a relay that alone keeps the central boiler on (documentation/en/technical_doc.md,
3.8): Versatile Thermostat counts a relay for its boiler only while it is on, so the boiler goes
off in each off part of SmartPI's cycle. That off part is the cycle's own, not a lack of heat: the
boiler cuts the room's heat only once the relay has been on for 5 s with the boiler off, and gives
it back only while both are on. The real handler and SmartPI's real calculation, driven by a small
event loop: the relay's switching in its cycle, the boiler's changes (seen by the handler's
listeners), SmartPI's calculations every 60 s, and the checks the handler schedules."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fakes.fake_thermostat_runtime import FakeThermostatRuntime
from helpers import force_smartpi_stable_mode

from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.boiler_call import (
    BOILER_BLINK_S,
    BOILER_PAUSE_CONFIRM_S,
)
from custom_components.vtherm_smartpi.handler import SmartPIHandler

DEAD = 780.0
RECALC = 60.0
FOLLOW_S = 0.01  # Versatile Thermostat switches its boiler this long after the relay


class _Loop:
    """The relay's cycle, the boiler, SmartPI's calculations and the handler's scheduled checks, in
    time order, on one monotonic clock."""

    def __init__(self, handler: SmartPIHandler, temp: Callable[[float], float] = lambda _at: 19.0) -> None:
        self.handler = handler
        self.temp = temp
        self.now = 0.0
        self.boiler = False
        self.timers: list[list] = []
        handler._boiler_listener_remove = MagicMock()  # the boiler's changes are followed
        handler._thermostat_listener_remove = MagicMock()  # and the thermostat's

    def later(self, _hass, delay: float, action) -> Callable[[], None]:
        timer = [self.now + delay, action, True]
        self.timers.append(timer)
        return lambda: timer.__setitem__(2, False)

    def _patched(self):
        return (
            patch("custom_components.vtherm_smartpi.handler.time.monotonic", return_value=self.now),
            patch("custom_components.vtherm_smartpi.algo.time.monotonic", return_value=self.now),
            patch("custom_components.vtherm_smartpi.smartpi.deadtime_estimator.time.monotonic", return_value=self.now),
            patch("custom_components.vtherm_smartpi.handler.central_boiler_on", return_value=self.boiler),
            patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"),
            patch("custom_components.vtherm_smartpi.handler.async_call_later", side_effect=self.later),
            patch.object(self.handler, "_async_save", new_callable=AsyncMock),
        )

    async def _at(self, at: float, act) -> None:
        while True:
            due = sorted((t for t in self.timers if t[2] and t[0] <= at), key=lambda t: t[0])
            if not due:
                break
            timer = due[0]
            timer[2] = False
            self.now = timer[0]
            p = self._patched()
            with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
                timer[1](None)
        self.now = at
        self.handler._thermostat.current_temperature = self.temp(at)
        p = self._patched()
        with p[0], p[1], p[2], p[3] as boiler_patch, p[4], p[5], p[6]:
            self._boiler_patch = boiler_patch
            result = act()
            if result is not None:
                await result

    async def run(self, events: Iterable[tuple[float, str, object]], watch: Callable[[float, str], None] | None = None) -> None:
        """Each event in time order: ("calc", command), ("relay", on) or ("boiler", on)."""
        algo = self.handler._thermostat.prop_algorithm
        for at, kind, value in sorted(events, key=lambda e: (e[0], {"calc": 0, "relay": 1, "boiler": 2}[e[1]])):
            # The checks due before the event run first, on the state before it.
            if kind == "calc":

                def act(v=value):
                    algo._committed_on_percent = v
                    return self.handler.control_heating(force=False)

            elif kind == "relay":

                def act(v=value):
                    self.handler._thermostat.is_device_active = v
                    self.handler._on_thermostat_change(None)

            else:

                def act(v=value):
                    self.boiler = v
                    self._boiler_patch.return_value = v
                    self.handler._on_boiler_change(None)

            await self._at(at, act)
            if watch is not None:
                watch(at, kind)


def _relay_handler(scheduler: bool = True) -> SmartPIHandler:
    """A learned room behind a relay (dead times 780 s, reliable, six detections each), at rest;
    without ``scheduler``, a thermostat without a cycle scheduler (a climate underlying)."""
    thermostat = FakeThermostatRuntime()
    thermostat.is_used_by_central_boiler = True
    if scheduler:
        thermostat.cycle_scheduler.is_valve_mode = False
    else:
        thermostat.cycle_scheduler = None
    handler = SmartPIHandler(thermostat)
    with patch("custom_components.vtherm_smartpi.handler.async_dispatcher_send"):
        handler.init_algorithm()
    algo = thermostat.prop_algorithm
    force_smartpi_stable_mode(algo)
    est = algo.dt_est
    est.deadtime_heat_s = est.deadtime_cool_s = DEAD
    est.deadtime_heat_reliable = est.deadtime_cool_reliable = True
    est._history_heat.extend([DEAD] * 6)
    est._history_cool.extend([DEAD] * 6)
    est.state, est.last_power, est.last_start_time, est.last_stop_time = "OFF", 0.0, None, -10_000.0
    return handler


def _cycles(cycle_s: float, duty: float, start: float, end: float) -> list[tuple[float, str, object]]:
    """SmartPI's relay in cycles of ``cycle_s`` at ``duty`` from ``start`` to ``end``: on at each
    cycle's start, off after ``duty`` of it; a calculation every minute at that command."""
    events: list[tuple[float, str, object]] = []
    at = start
    while at < end:
        events.append((at, "relay", True))
        if duty < 1.0:
            events.append((at + duty * cycle_s, "relay", False))
        at += cycle_s
    calc = start
    while calc < end:
        events.append((calc, "calc", duty))
        calc += RECALC
    return events


def _following(events: list[tuple[float, str, object]]) -> list[tuple[float, str, object]]:
    """The boiler follows the relay, kept on by it alone, a moment after each change."""
    return [(at + FOLLOW_S, "boiler", value) for at, kind, value in events if kind == "relay"]


@pytest.mark.parametrize(
    ("cycle_s", "duty"),
    [(300.0, 0.6), (3600.0, 0.58), (300.0, 0.99)],
    ids=["5-minute cycle", "60-minute cycle (an underfloor loop)", "off part of 3 s"],
)
async def test_a_relay_cycling_alone_with_the_boiler_is_its_own_cycle(cycle_s, duty, caplog) -> None:
    """The relay keeps the boiler on alone, which follows it: the room's heat starts once, with
    the boiler, and the relay's own off parts (25 minutes in an hour's cycle, or only 3 s) are no
    stop: the estimator sees SmartPI's command throughout, learning never pauses, and SmartPI logs
    no stop of heat and no blink."""
    caplog.set_level(logging.INFO)
    handler = _relay_handler()
    algo = handler._thermostat.prop_algorithm
    est = algo.dt_est
    loop = _Loop(handler)
    relay = _cycles(cycle_s, duty, 0.0, 3 * cycle_s)
    paused: list[float] = []
    powers: list[float] = []

    def watch(at: float, _kind: str) -> None:
        if algo.boiler_learning_paused:
            paused.append(at)
        if at > BOILER_BLINK_S + 1.0:
            powers.append(algo._dead_time_power())

    await loop.run(relay + _following(relay), watch)

    assert est.last_start_time == pytest.approx(FOLLOW_S) and est.last_stop_time == -10_000.0
    assert not paused
    assert set(powers) == {duty}
    assert "heat stopped" not in caplog.text and "blink" not in caplog.text


async def test_a_cut_from_outside_with_a_cycling_relay_holds(caplog) -> None:
    """The boiler does not start for the relay (another room's demand is missing): the room gets no
    heat, in the relay's on parts as in its off parts. The estimator sees no power throughout, and
    learning pauses a calculation period after the call and stays paused."""
    caplog.set_level(logging.INFO)
    handler = _relay_handler()
    algo = handler._thermostat.prop_algorithm
    loop = _Loop(handler)
    paused: list[tuple[float, bool]] = []
    powers: list[float] = []

    def watch(at: float, _kind: str) -> None:
        paused.append((at, algo.boiler_learning_paused))
        powers.append(algo._dead_time_power())

    await loop.run(_cycles(300.0, 0.6, 0.0, 900.0), watch)

    assert set(powers) == {0.0} and algo.dt_est.last_power == 0.0
    assert all(p for at, p in paused if at >= BOILER_PAUSE_CONFIRM_S + 1.0)
    assert not any(p for at, p in paused if at < BOILER_PAUSE_CONFIRM_S)
    assert caplog.text.count("SmartPI learning paused") == 1 and "learning resumed" not in caplog.text


async def test_after_a_cut_the_heat_starts_with_the_relay_not_with_the_boiler() -> None:
    """An hour's cycle at 50 %: the boiler does not start for the relay's first on part (a cut);
    another room starts it at 2400 s, in the relay's off part. The room gets heat only when the
    relay turns on again, at 3600 s: the cut holds until then, and the heat's start counts from it,
    not 20 minutes too early. (At 50 % the estimator times no response: its 80 % floor.)"""
    handler = _relay_handler()
    algo = handler._thermostat.prop_algorithm
    est = algo.dt_est
    loop = _Loop(handler)
    events = _cycles(3600.0, 0.5, 0.0, 4800.0) + [(2400.0, "boiler", True)]
    seen: dict[float, float] = {}

    def watch(at: float, _kind: str) -> None:
        seen[at] = algo._dead_time_power()

    await loop.run(events, watch)

    assert all(power == 0.0 for at, power in seen.items() if at < 3600.0)
    assert seen[3600.0 + RECALC] == 0.5
    assert est.last_start_time == 3600.0


async def test_a_relay_whose_boiler_waits_for_its_activation_delay_starts_from_the_boiler() -> None:
    """The relay turns on at the call; the boiler starts 120 s later (Versatile Thermostat's
    activation delay) and follows the relay after that. The room waits for the boiler: learning
    pauses meanwhile and resumes at the boiler's start, from which the heat's start counts."""
    handler = _relay_handler()
    algo = handler._thermostat.prop_algorithm
    est = algo.dt_est
    loop = _Loop(handler)
    relay = _cycles(300.0, 1.0, 0.0, 600.0)
    events = relay + [(120.0, "boiler", True)]
    paused_at: list[float] = []

    def watch(at: float, _kind: str) -> None:
        if algo.boiler_learning_paused:
            paused_at.append(at)

    await loop.run(events, watch)

    assert paused_at and max(paused_at) <= 120.0 and not algo.boiler_learning_paused
    assert est.last_start_time == 120.0 and est.heat_start_time == 120.0


async def test_a_relay_that_the_boiler_stops_following_is_cut_5_s_after_it_turns_on(caplog) -> None:
    """The relay heats the room, the boiler following it, for 600 s; then the boiler stays off as
    the relay turns on again (the threshold is no longer reached): the heat stops 5 s after the
    relay's start, once Versatile Thermostat has had the time to start the boiler."""
    caplog.set_level(logging.INFO)
    handler = _relay_handler()
    est = handler._thermostat.prop_algorithm.dt_est
    loop = _Loop(handler)
    relay = _cycles(300.0, 0.6, 0.0, 900.0)
    boiler = [e for e in _following(relay) if e[0] < 600.0]
    await loop.run(relay + boiler)

    assert est.last_stop_time == 600.0 + BOILER_BLINK_S and est.last_power == 0.0
    assert caplog.text.count("heat stopped with the central boiler") == 1


async def test_a_relay_s_own_stop_after_a_settled_heating_times_its_passive_response() -> None:
    """The relay heats the room for 1200 s in cycles at 90 % (off 30 s in each), the boiler
    following it, then SmartPI stops calling: the heating counts from the first start, not from
    the relay's last on part, so it has settled (longer than the room's 780 s) and the stop times
    the room's passive response."""
    handler = _relay_handler()
    est = handler._thermostat.prop_algorithm.dt_est
    loop = _Loop(handler)
    relay = _cycles(300.0, 0.9, 0.0, 1200.0)
    await loop.run(relay + _following(relay) + [(1200.0, "calc", 0.0)])

    assert est.last_start_time == pytest.approx(FOLLOW_S)
    assert (est.state, est.cool_start_time, est.last_stop_time) == ("WAITING_COOL_RESPONSE", 1200.0, 1200.0)


async def test_a_thermostat_without_a_cycle_scheduler_follows_the_boiler_as_read() -> None:
    """A climate underlying has no cycle scheduler: the relay's rule does not apply, and the boiler
    going off while SmartPI calls stops the room's heat at once, its device off or not (SmartPI's
    calculation is not run here: the test thermostat needs a scheduler for it)."""
    handler = _relay_handler(scheduler=False)
    algo = handler._thermostat.prop_algorithm
    est = algo.dt_est
    algo._committed_on_percent = 1.0
    est.last_power = 1.0
    loop = _Loop(handler)
    await loop.run([(0.0, "boiler", True), (100.0, "boiler", False)])

    assert not handler._drives_a_relay()
    assert est.last_stop_time == 100.0 and algo.boiler_on is False and algo._dead_time_power() == 0.0


def test_only_a_relay_s_cycle_scheduler_out_of_valve_mode_is_a_relay() -> None:
    """A switch's cycle scheduler is a relay; a valve's is not, nor a thermostat without one."""
    handler = _relay_handler()
    assert handler._drives_a_relay()
    handler._thermostat.cycle_scheduler.is_valve_mode = True
    assert not handler._drives_a_relay()
    handler._thermostat.cycle_scheduler = None
    assert not handler._drives_a_relay()
    assert isinstance(handler._thermostat.prop_algorithm, SmartPI)
