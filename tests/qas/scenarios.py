"""The QAS scenarios: SmartPI's learning pause below Versatile Thermostat's central boiler
threshold (documentation/en/technical_doc.md, 3.8), on a prepared instance.

West and East heat with water radiators the central boiler counts; North with an electric heater
it does not count. What a scenario shares with the others, the start state, the steps and their
checks, the timing and the trace, is in ``steps.py``.

Each scenario is registered with its roles, how its learning starts (``fresh`` or ``learned``),
North's setpoint, its time, whether it is in the minimum set, and its counterpart without the
bench; the catalogue in README.md lists the
same (checked by tests/test_qas_files.py). Its docstring says it in one line: the start state →
the event → the expected result. A scenario's time is the sum of its waits (``Wait``), for each
of its roles: "typical" is what SmartPI's timing gives, "maximum" the sum of the waits' limits.
Every wait ends as soon as its condition holds.

"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from pack import ROLES, SMARTPI, VT, Role, room
from steps import (
    AFTER_RESTART,
    BASE_SETPOINT,
    BOILER_PAUSE_LINE,
    BOILER_STOPS,
    CALLING,
    AT_ONCE_S,
    CLOCK_MARGIN_S,
    COOL_DETECTED_LINE,
    HEAT_DETECTED_LINE,
    CONTEXT,
    COUNTED,
    CYCLE_S,
    DETACHED_AFTER_S,
    EAST,
    HEAT_STOP_LINE,
    HOLD,
    KEYS,
    MARGIN_S,
    PUBLISHED_LATE_S,
    NAMES,
    NORTH,
    NO_PAUSE,
    PAUSE_AFTER_RESTART,
    PAUSE_CONFIRM_S,
    PAUSE_EXTERNAL,
    PAUSE_ON_SETPOINT,
    POLL_S,
    PUBLISHED,
    RECALC_S,
    RESTART,
    RESUME_ON_SETPOINT,
    RESUME_ON_STOP_LEARNED,
    SETTLED_S,
    START_STATE,
    STARTS,
    STOPPED,
    TARGET_CHANGE_LINE,
    VALVE_CLOSES,
    WEST,
    WINDOW_CLOSES,
    WAITING_COOL,
    WAITING_HEAT,
    WINDOW_OPENS,
    WITHIN_S,
    Bench,
    Wait,
    boiler_starts,
    calls,
    clock,
    instance_log,
    logged_cool_dead_times,
    logged_cool_starts,
    logged_heat_stops,
    logged_pauses,
    logged_target_changes,
    of,
    published_at,
    resume_when_the_boiler_starts,
    seconds,
    times,
    total,
    transitions,
)

STARTUP_NOTICE = "External proportional algorithm 'smartpi' not yet registered"
PAUSE_TESTS = "test_boiler_call.py"
END_TO_END = "test_boiler_call.py, test_qas_instance.py"
THREE = ("boiler-count", "boiler-power", "boiler-delay")
LEVELS = ("boiler-levels", "boiler-levels-delay")


def longest(*waits: Wait) -> Wait:
    """Waits watched at once."""
    return Wait(max(w.typical_s for w in waits), max(w.maximum_s for w in waits))


# What the steps take, for a role (steps.Bench.change).
def west_alone(role: Role) -> Wait:
    return PAUSE_ON_SETPOINT


def east_joins(role: Role) -> Wait:
    """East calls with West paused: the boiler starts and West resumes once it is on; East is
    watched for a pause (with an activation delay, both are paused until the boiler is on)."""
    if role.boiler_delay_s:
        return longest(resume_when_the_boiler_starts(role), boiler_starts(role))
    return total(longest(resume_when_the_boiler_starts(role), boiler_starts(role)), NO_PAUSE)


def east_leaves(role: Role) -> Wait:
    """East stops with West calling: the boiler stops at once, West pauses."""
    return longest(VALVE_CLOSES, BOILER_STOPS, PAUSE_EXTERNAL)


def west_leaves(role: Role, learned: bool = False) -> Wait:
    """West stops calling: a pause ends at once, for a learned West at its next calculation."""
    return longest(RESUME_ON_STOP_LEARNED if learned else RESUME_ON_SETPOINT, BOILER_STOPS)


def both_start(role: Role) -> Wait:
    """West and East start together: the boiler starts (with an activation delay, both are paused
    until it is on), then both are watched for five calculations."""
    return total(longest(boiler_starts(role), resume_when_the_boiler_starts(role) if role.boiler_delay_s else boiler_starts(role)), Wait(5 * RECALC_S, 5 * RECALC_S))


# --- the registry -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    id: str
    run: Callable[[Bench], Awaitable[None]]
    roles: tuple[str, ...]
    time: Callable[[Role], Wait]  # its steps' waits, without the start state
    counterpart: str
    minimum: bool
    depends: str  # what its time depends on, beyond SmartPI's timing
    start: str  # "fresh" or "learned"
    north: float  # North's setpoint
    trace: bool
    boiler_at_end: bool  # the closing start state waits for VTherm's boiler off (``closing``), also after a restart
    paused_at_end: bool  # West paused at the end: the closing start state waits for its resume

    def closing(self, role: Role) -> Wait:
        """The start state after the scenario (``steps.Bench.settled``): VTherm's boiler off for the
        role's delay plus ``SETTLED_S`` when the scenario ends with it on or just after a restart,
        which starts VTherm's boiler-off time again (BP0 after the preparation's, BP9), and a
        learned West paused at the end resumes at its next calculation once it stops calling; both
        watched at once."""
        boiler = role.boiler_delay_s + SETTLED_S if self.boiler_at_end else 0.0
        resume = RESUME_ON_STOP_LEARNED.typical_s if self.paused_at_end and self.start == "learned" else 0.0
        return Wait(max(boiler, resume), START_STATE.maximum_s)

    def time_for(self, role: Role) -> Wait:
        return total(self.closing(role), self.time(role))

    @property
    def times(self) -> list[Wait]:
        return [self.time_for(ROLES[name]) for name in self.roles]

    @property
    def detached(self) -> bool:
        return max(w.maximum_s for w in self.times) > DETACHED_AFTER_S

    @property
    def checks(self) -> str:
        return " ".join((self.run.__doc__ or "").split())

    @property
    def parts(self) -> list[str]:
        """The start state, the event, the expected result."""
        return [part.strip() for part in self.checks.split("→")]


SCENARIOS: dict[str, Scenario] = {}


def scenario(
    id: str,  # noqa: A002
    roles: tuple[str, ...],
    time: Wait | Callable[[Role], Wait],  # noqa: A002 — the catalogue's columns
    counterpart: str,
    minimum: bool = False,
    depends: str = "",
    start: str = "fresh",
    north: float = CALLING,
    trace: bool = True,
    boiler_at_end: bool = False,
    paused_at_end: bool = False,
):
    def register(run: Callable[[Bench], Awaitable[None]]) -> Callable[[Bench], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"{id} registered twice")
        if start not in STARTS:
            raise ValueError(f"{id}: start {start!r}")
        timing = time if callable(time) else (lambda _role, w=time: w)
        SCENARIOS[id] = Scenario(id, run, roles, timing, counterpart, minimum, depends, start, north, trace, boiler_at_end, paused_at_end)
        return run

    return register


# --- BP0: the instance ------------------------------------------------------------------


@scenario(
    "BP0",
    tuple(ROLES),
    Wait(0.0, 3 * 300.0),
    "test_qas_instance.py, test_published_diagnostics_schema.py",
    minimum=True,
    boiler_at_end=True,
)
async def BP0(b: Bench) -> None:
    """After the preparation's restart → the instance is read → every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet, VTherm's central boiler agrees with the test rooms' boiler switch, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published."""
    for domain in (SMARTPI, VT):
        for entry in await b.api.entries(domain):
            b.check(entry.get("state") == "loaded", f"{domain} {entry['title']!r}: {entry.get('state')}")
    titles = [e["title"] for e in await b.api.entries(SMARTPI)]
    b.check(titles == ["SmartPI defaults"], f"SmartPI entries: {titles}")
    for climate in KEYS:
        took, snap = await b.until(lambda s, c=climate: bool(of(s, c, "phase")), 300.0)
        b.check(took is not None, f"{climate}: SmartPI's diagnostics, phase {of(snap, climate, 'phase')}")
        b.check(of(snap, climate, "enabled") is True, f"{climate}: learning.enabled {of(snap, climate, 'enabled')}")
        b.check(isinstance(of(snap, climate, "paused"), bool), f"{climate}: learning.boiler_paused {of(snap, climate, 'paused')}")
    snap = await b.snapshot()
    b.check(snap["boiler"] == snap["switch"], f"VTherm's central boiler {snap['boiler']}, the test rooms' boiler switch {snap['switch']}")
    issues = (await b.r.ha.ws_cmd({"type": "repairs/list_issues"}) or {}).get("issues", [])
    repairs = sorted(i["issue_id"] for i in issues if i.get("domain") == SMARTPI)
    b.check(not repairs, f"SmartPI's repairs: {repairs}")
    lines = []
    for entry in await b.r.ha.ws_cmd({"type": "system_log/list"}):
        message = " | ".join(entry.get("message") or [])
        if str(entry.get("name", "")).startswith((f"custom_components.{SMARTPI}", f"custom_components.{VT}")):
            if STARTUP_NOTICE in message:
                b.note(f"known at startup: {message[:120]}")
            else:
                lines.append(f"{entry.get('level')} {entry.get('name')}: {message[:160]}")
    b.check(not lines, f"warnings and errors of SmartPI and VTherm: {len(lines)}")
    for line in lines[:10]:
        b.note(line)


# --- BP1-BP11: the pause, step by step ---------------------------------------------------


@scenario("BP1", THREE, lambda role: total(west_alone(role), HOLD), END_TO_END, minimum=True)
async def BP1(b: Bench) -> None:
    """The start state → West calls alone, below the threshold, the boiler off → West pauses once it has called with the boiler off for a calculation period (60 s), not before; VTherm's boiler stays off; East and North (not counted) do not pause; West's valve stays open."""
    await b.west_calls()
    await b.hold()


@scenario("BP2", THREE, lambda role: total(west_alone(role), east_joins(role)), END_TO_END, minimum=True, boiler_at_end=True)
async def BP2(b: Bench) -> None:
    """West calls alone and pauses → East calls too, the threshold is reached → VTherm's boiler starts (with an activation delay, 120 s later) and West resumes at that moment, not before; East does not pause (with the delay, it pauses too while the boiler waits)."""
    await b.west_calls()
    await b.east_calls()


@scenario("BP3", THREE, lambda role: total(west_alone(role), west_leaves(role)), PAUSE_TESTS, minimum=True)
async def BP3(b: Bench) -> None:
    """West calls alone and pauses → West stops calling → West resumes at once, though the threshold is still not reached; the boiler stays off."""
    await b.west_calls()
    await b.west_stops()


SENSORS = Wait(40.0, 180.0)  # Home Assistant reloads VTherm's entry about 30 s after a registry change


@scenario("BP4", ("boiler-count",), total(SENSORS, Wait(WITHIN_S, WITHIN_S), SENSORS, PAUSE_EXTERNAL), PAUSE_TESTS)
async def BP4(b: Bench) -> None:
    """VTherm's central boiler state disabled, so SmartPI cannot read it (VTherm, which needs that entity, does not drive the boiler meanwhile) → West calls alone → West does not pause while its valve is open; once the boiler's state is back, off, it pauses."""
    boiler = b.ids.boiler

    async def disable(by: str | None) -> None:
        await b.r.ha.ws_cmd({"type": "config/entity_registry/update", "entity_id": boiler, "disabled_by": by})

    try:
        await disable("user")
        took, _ = await b.until(lambda s: s["boiler"] is None, 180.0)
        b.check(took is not None, f"VTherm's boiler state gone after {seconds(took)} s: {boiler}")
        b.note("— West calls, the boiler's state unreadable")
        await b.calls_at(WEST, CALLING)
        seen = await b.watch(WITHIN_S)
        found = [clock(s) for s in seen if of(s, WEST, "paused") is not False]
        b.check(not found, f"West alone, the boiler's state unreadable: not paused for {WITHIN_S:.0f} s ({', '.join(found[:5]) or 'never paused'})")
        # The pause starts only while SmartPI calls: a West not calling would pass too.
        b.check(all(calls(s, WEST) for s in seen[len(seen) // 2 :]), f"West calls meanwhile: valve opening {of(seen[-1], WEST, 'opening')} %")
    finally:
        await disable(None)
        took, snap = await b.until(lambda s: s["boiler"] is not None, 180.0)
        b.check(took is not None, f"VTherm's boiler state back: {snap.get('boiler')}")
    if WEST in b.calling:
        took = await b.reach({"West paused": (b.paused_is(WEST, True), PAUSE_EXTERNAL)}, time.time())
        await b.after("the boiler's state read again")


@scenario("BP5", THREE, lambda role: total(PUBLISHED, west_alone(role), east_joins(role), PUBLISHED), PAUSE_TESTS, boiler_at_end=True)
async def BP5(b: Bench) -> None:
    """The user's learning paused (set_smartpi_learning off) → West calls alone, then East too → West's boiler pause starts and ends as without it, learning.enabled stays off throughout; the user's setting is put back."""

    async def learning(enabled: bool) -> None:
        await b.api.call(SMARTPI, "set_smartpi_learning", {"entity_id": WEST, "learning_enabled": enabled})
        took, _ = await b.until(lambda s: of(s, WEST, "enabled") is enabled, MARGIN_S)
        b.check(took is not None, f"the user's learning setting is {'on' if enabled else 'off'}")

    try:
        await learning(False)
        await b.west_calls()
        snap = await b.snapshot()
        b.check(of(snap, WEST, "enabled") is False, f"paused: learning.enabled {of(snap, WEST, 'enabled')}")
        await b.east_calls()
        snap = await b.snapshot()
        b.check(of(snap, WEST, "enabled") is False, f"resumed: learning.enabled {of(snap, WEST, 'enabled')}")
    finally:
        await learning(True)


BOILER_OFF_AT_ONCE = Wait(0.0, 30.0)  # VTherm acts on East's valve as it closes
AFTER_THE_STOP = Wait(5 * RECALC_S, 5 * RECALC_S)  # BP6: West watched after its stop ends its pause


@scenario(
    "BP6",
    THREE,
    lambda role: total(west_alone(role), east_joins(role), east_leaves(role), west_leaves(role), AFTER_THE_STOP),
    PAUSE_TESTS,
)
async def BP6(b: Bench) -> None:
    """West and East call, the boiler on → East stops, the demand drops below the threshold with West heating → VTherm's boiler and its switch go off at once (also with an activation delay), West pauses two calculations later; West stopping then resumes it, and as its heat stopped with the boiler, SmartPI arms no detection of West's passive response (never WAITING_COOL_RESPONSE for five calculations)."""
    await b.west_calls()
    await b.east_calls()
    await b.change(
        "East stops calling, West heating",
        lambda: b.calls_at(EAST, STOPPED),
        own=EAST,
        goals={
            "East's valve closed": (lambda s: of(s, EAST, "valve") == 0.0, VALVE_CLOSES),
            "the boiler's switch off at once": (lambda s: s["switch"] == "off", BOILER_OFF_AT_ONCE),
        },
    )
    await b.west_stops()
    seen = await b.watch(AFTER_THE_STOP.maximum_s)
    armed = [clock(s) for s in seen if of(s, WEST, "dt_state") == WAITING_COOL]
    b.check(not armed, f"West's stop ends its pause: no detection of its passive response for {AFTER_THE_STOP.maximum_s:.0f} s ({', '.join(armed[:3]) or 'none seen'})")


HEAT_UP = Wait(0.0, 3600.0)  # BP7: the calibration's heating step (see its note)
PAUSED_READINGS = Wait(10 * RECALC_S, 10 * RECALC_S)  # BP7: five readings, two calculations apart


@scenario(
    "BP7",
    THREE,
    lambda role: total(PUBLISHED, HEAT_UP, PAUSE_ON_SETPOINT, PAUSED_READINGS, east_joins(role)),
    PAUSE_TESTS,
    depends="typical when West starts below the calibration's low threshold (setpoint - 0.3 degC), as its setpoint is set 1 degC above the room",
    start="learned",
    boiler_at_end=True,
)
async def BP7(b: Bench) -> None:
    """West learned, its setpoint 1 degC above its room → a forced calibration heats with the boiler off → learning pauses: the calibration arms no dead-time detection and the learned heating dead time stays unchanged; once East calls and the boiler is on, learning resumes."""
    snap = await b.snapshot()
    b.check(of(snap, WEST, "phase") == "Stable", f"West learned: phase {of(snap, WEST, 'phase')}, a {of(snap, WEST, 'a')}")
    learned = of(snap, WEST, "deadtime")
    indoor = of(snap, WEST, "indoor") or BASE_SETPOINT
    try:
        await b.setpoints(**{WEST: round(indoor + 1.0, 1)})
        await b.api.call(SMARTPI, "force_smartpi_calibration", {"entity_id": WEST})
        took, snap = await b.until(lambda s: of(s, WEST, "calibration") == "HeatUp", HEAT_UP.maximum_s, 30.0)
        b.check(took is not None, f"the calibration's heating step after {seconds(took)} s")
        b.calling.add(WEST)  # the heating step opens the valve fully
        took, _ = await b.until(b.paused_is(WEST, True), WITHIN_S)
        b.check(took is not None, f"heating alone with the boiler off: paused after {seconds(took)} s")
        for _ in range(5):
            snap = await b.snapshot()
            b.check(
                of(snap, WEST, "dt_state") != WAITING_HEAT and of(snap, WEST, "deadtime") == learned,
                f"paused: dead-time detection {of(snap, WEST, 'dt_state')} (not waiting for a response), heating dead time {of(snap, WEST, 'deadtime')} s (learned {learned} s)",
            )
            await asyncio.sleep(2 * RECALC_S)
        await b.east_calls()
    finally:
        # Off and on again cancels the calibration; the runner then puts the start state back.
        await b.hvac(WEST, "off")
        await b.hvac(WEST, "heat")


@scenario("BP8", THREE, both_start, END_TO_END, minimum=True, boiler_at_end=True)
async def BP8(b: Bench) -> None:
    """The start state → West and East start calling together → VTherm's boiler starts and neither pauses, though VTherm counts their devices a moment after SmartPI first reads the boiler's state; with an activation delay (120 s) the boiler stays off meanwhile, both pause and resume once it is on."""

    async def both() -> None:
        await b.calls_at(WEST, CALLING)
        await b.calls_at(EAST, CALLING)

    await b.change("West and East call together", both)
    await b.no_pause(COUNTED, 5 * RECALC_S, "the boiler starts")


def kept(snap: dict[str, Any], climate: str) -> dict[str, Any]:
    return {name: of(snap, climate, name) for name in ("a", "b", "deadtime", "accepted_a", "accepted_b")}


@scenario(
    "BP9",
    THREE,
    lambda role: total(west_alone(role), RESTART, PAUSE_AFTER_RESTART),
    PAUSE_TESTS,
    start="learned",
    boiler_at_end=True,  # the restart starts VTherm's boiler-off time again
    paused_at_end=True,
)
async def BP9(b: Bench) -> None:
    """West learned, calling alone and paused → Home Assistant restarts → once SmartPI runs again, the pause returns within two calculations; nothing is learned in between (accepted updates unchanged, no dead-time detection armed); a, b and the dead time are kept."""
    await b.west_calls()
    before = kept(await b.snapshot(), WEST)
    await b.restart()
    seen: list[dict[str, Any]] = []

    def watch(s: dict[str, Any]) -> bool:
        seen.append(s)
        return of(s, WEST, "paused") is True

    took, snap = await b.until(watch, PAUSE_EXTERNAL.maximum_s)
    b.check(took is not None, f"the pause returns after {seconds(took)} s")
    armed = [clock(s) for s in seen if of(s, WEST, "dt_state") == WAITING_HEAT]
    b.check(not armed, f"no dead-time detection armed after the restart ({', '.join(armed[:3]) or 'none seen'})")
    for name in ("accepted_a", "accepted_b"):
        values = {of(s, WEST, name) for s in seen if of(s, WEST, name) is not None}
        b.check(values == {before[name]}, f"{name}: {before[name]} before, {sorted(values, key=str)} since")
    b.check(kept(snap, WEST) == before, f"a, b and the dead time kept: {kept(snap, WEST)} (before {before})")
    await b.after("after the restart")


def lowered(role: Role) -> float:
    """BP10: a threshold West alone reaches: 1 device, or 500 W."""
    return 1.0 if role.count_threshold > 0 else 500.0


@scenario(
    "BP10",
    THREE,
    lambda role: total(west_alone(role), longest(resume_when_the_boiler_starts(role), boiler_starts(role)), longest(PAUSE_EXTERNAL, BOILER_STOPS)),
    PAUSE_TESTS,
)
async def BP10(b: Bench) -> None:
    """West calls alone and pauses → VTherm's threshold lowered to 1 device (or 500 W), then back → lowered, the boiler starts (with an activation delay, 120 s later), though only West calls, and West resumes at that moment; back, West pauses again two calculations later and the boiler stops at once."""
    await b.west_calls()
    _, original = await b.threshold_entity()
    try:
        await b.threshold_set(lowered(b.role))
        await b.threshold_set(original)
    finally:
        entity_id, _ = await b.threshold_entity()
        await b.api.call("number", "set_value", {"entity_id": entity_id, "value": original})


@scenario(
    "BP11",
    THREE,
    lambda role: total(west_alone(role), RESUME_ON_SETPOINT, PAUSE_ON_SETPOINT, WINDOW_OPENS, WINDOW_CLOSES),
    PAUSE_TESTS,
)
async def BP11(b: Bench) -> None:
    """West calls alone and pauses → West turned off and on, then its window opened and closed → off, its pause ends within a calculation (SmartPI does not call); heating again, the pause returns after a calculation period below the threshold; window open, VTherm turns West off after 30 s and the pause ends; window closed, West heats again and pauses two calculations later."""
    await b.west_calls()
    try:
        took = await b.west_off()
        ended = took.get("West resumed")
        b.check(ended is not None and ended <= RECALC_S, f"West off: the pause ended within a calculation ({seconds(ended)} s)")
        await b.west_on()
        await b.window(True)
        await b.window(False)
    finally:
        await b.api.call("input_boolean", "turn_off", {"entity_id": "input_boolean.west_window"})
        await b.hvac(WEST, "heat")



# --- BP13-BP20: thresholds of power, partly open valves, restarts --------------------------------


IDLE_PAUSE = Wait(20 * 60.0, 20 * 60.0)  # BP13 A′


def learning_of(snap: dict[str, Any], climate: str) -> dict[str, Any]:
    return {name: of(snap, climate, name) for name in ("accepted_a", "accepted_b", "learn_ok", "deadtime")}


@scenario(
    "BP13",
    LEVELS,
    lambda role: total(west_alone(role), IDLE_PAUSE, east_joins(role), east_leaves(role), west_leaves(role, learned=True)),
    PAUSE_TESTS + ", test_qas_instance.py",
    start="learned",
)
async def BP13(b: Bench) -> None:
    """West 300 W and East 900 W learned, a power threshold of 600 W → A West calls (300 W), A′ 20 minutes, B East calls (1200 W), C East stops (300 W), D West stops → A: the boiler stays off and West pauses after a calculation period with the boiler off; A′: the pause holds without a break, the boiler never starts, the room cools and nothing is learned; B: the boiler starts (with a delay, 120 s later) and West resumes at that moment; East does not pause (with the delay, it pauses too while the boiler waits); C: the boiler goes off at once, West pauses two calculations later, and nothing from the gap between them is learned; D: the pause ends."""
    await b.west_calls()
    start = await b.snapshot()
    b.note("— A′ West paused for 20 minutes")
    seen = await b.watch(IDLE_PAUSE.typical_s, 10.0)
    breaks = [clock(s) for s in seen if of(s, WEST, "paused") is not True]
    b.check(not breaks, f"A′ the pause holds for {IDLE_PAUSE.typical_s / 60:.0f} min without a break ({', '.join(breaks[:5]) or 'none'})")
    on = [clock(s) for s in seen if s["boiler"] != "off" or s["switch"] != "off"]
    b.check(not on, f"A′ the boiler never starts ({', '.join(on[:5]) or 'never'})")
    end = seen[-1] if seen else start
    b.check(
        (of(start, WEST, "indoor") or 0) - (of(end, WEST, "indoor") or 0) >= 0.1,
        f"A′ West's room cools though it calls: {of(start, WEST, 'indoor')} → {of(end, WEST, 'indoor')} degC",
    )
    b.check(learning_of(end, WEST) == learning_of(start, WEST), f"A′ nothing learned: {learning_of(start, WEST)} → {learning_of(end, WEST)}")
    await b.east_calls()
    before = await b.snapshot()
    took = await b.change(
        "C East stops (300 W)",
        lambda: b.calls_at(EAST, STOPPED),
        own=EAST,
        goals={
            "East's valve closed": (lambda s: of(s, EAST, "valve") == 0.0, VALVE_CLOSES),
            "the boiler's switch off at once": (lambda s: s["switch"] == "off", BOILER_OFF_AT_ONCE),
        },
    )
    after = await b.snapshot()
    gap = (took.get("West paused") or 0.0) - (took.get("VTherm's boiler off") or 0.0)
    b.check(
        learning_of(after, WEST) == learning_of(before, WEST),
        f"C nothing learned in the {gap:.0f} s between the boiler's stop and West's pause: {learning_of(before, WEST)} → {learning_of(after, WEST)}",
    )
    b.note(f"C West's window outcomes, before → after: {of(before, WEST, 'outcomes')} → {of(after, WEST, 'outcomes')}")
    await b.west_stops()




def switches(samples: Sequence[dict[str, Any]], key: str) -> int:
    return max(len(transitions(samples, key)) - 1, 0)


FLICKER = ((RECALC_S, 10), (10.0, 30))  # East switched every calculation, then every 10 s: (period, switches)
FLICKER_S = sum(period * n for period, n in FLICKER)


@scenario(
    "BP17",
    ("boiler-count", "boiler-power"),
    lambda role: total(west_alone(role), Wait(FLICKER_S, FLICKER_S + MARGIN_S), PAUSE_EXTERNAL),
    PAUSE_TESTS,
    start="learned",
)
async def BP17(b: Bench) -> None:
    """West and East learned, West calling alone and paused → East switched on and off at the threshold, every calculation for 10 minutes, then every 10 s for 5 minutes, then off → no window that short reaches West's or East's learning (accepted updates and the heating dead times unchanged); it records the switches of the pause and of the boiler; West ends paused."""
    await b.west_calls()
    before = await b.snapshot()
    begin = len(b.trace.samples) if b.trace else 0
    for period, n in FLICKER:
        b.note(f"— East switched every {period:.0f} s, {n} times")
        for i in range(n):
            await b.setpoints(**{EAST: CALLING if i % 2 == 0 else STOPPED})
            await asyncio.sleep(period)
    await b.calls_at(EAST, STOPPED)
    await b.reach(
        {"East's valve closed": (lambda s: of(s, EAST, "valve") == 0.0, VALVE_CLOSES), "West paused": (b.paused_is(WEST, True), PAUSE_EXTERNAL)},
        time.time(),
    )
    after = await b.after("East off after the flicker")
    samples = list(b.trace.samples[begin:]) if b.trace else []
    for climate in COUNTED:
        b.check(
            learning_of(after, climate) == learning_of(before, climate),
            f"nothing from the short windows learned by {NAMES[climate]}: {learning_of(before, climate)} → {learning_of(after, climate)}",
        )
    b.note(f"switches: West's pause {switches(samples, 'west.paused')}, East's pause {switches(samples, 'east.paused')}, boiler {switches(samples, 'boiler')}, the boiler's switch {switches(samples, 'switch')}")


@scenario("BP18", THREE, both_start, END_TO_END, boiler_at_end=True)
async def BP18(b: Bench) -> None:
    """The start state → East calls, then West, and West's setpoint changes again at once (two of West's calculations a moment apart at the boiler's start) → neither pauses and the boiler starts, though two of West's calculations come a moment apart with the boiler still off; with an activation delay (120 s) both pause while the boiler waits and resume once it is on."""
    marks: list[float] = []

    async def quick() -> None:
        for climate, value in ((EAST, CALLING), (WEST, CALLING), (WEST, CALLING - 0.5)):
            await b.setpoints(**{climate: value})
            marks.append(time.time())
        b.calling.update(COUNTED)  # at 22.5 degC the valve is fully open too

    await b.change("East, West and West again, at once", quick)
    b.note(f"the three setpoints within {1000 * (marks[-1] - marks[0]):.0f} ms")
    await b.no_pause(COUNTED, 5 * RECALC_S, "the boiler starts")


def boiler_back(seen: Sequence[dict[str, Any]], since: float) -> float | None:
    """BP20: the seconds from ``since`` (the restart) until VTherm's boiler was on again, from the
    readings of the watch: the first with the boiler on, at the time VTherm gives for it (0 s when
    it never went off); None when it did not come back."""
    on = next((s for s in seen if s.get("boiler") == "on"), None)
    if on is None:
        return None
    at = published_at(on.get("boiler_since")) or on["t"]
    return max(at - since, 0.0)


@scenario(
    "BP20",
    THREE,
    lambda role: total(boiler_starts(role), RESTART, AFTER_RESTART, Wait(5 * RECALC_S, 5 * RECALC_S)),
    PAUSE_TESTS,
    start="learned",
    boiler_at_end=True,
)
async def BP20(b: Bench) -> None:
    """West and East learned, both calling, the boiler on, nothing paused → Home Assistant restarts → once SmartPI runs again, VTherm counts their devices again and the boiler comes back; neither pauses, but with an activation delay VTherm shows the boiler off for 120 s, both pause meanwhile and resume once it is back; a, b and the dead times are kept."""

    async def both() -> None:
        await b.calls_at(WEST, CALLING)
        await b.calls_at(EAST, CALLING)

    await b.change("West and East call", both)
    before = {c: kept(await b.snapshot(), c) for c in COUNTED}
    restarted_at = await b.restart()
    seen = await b.watch(5 * RECALC_S)  # covers the boiler's return too: its limit is within 300 s
    back = boiler_back(seen, restarted_at)
    wait = boiler_starts(b.role)
    b.check(back is not None and back <= RESTART.typical_s + wait.maximum_s, f"VTherm's boiler on again {seconds(back)} s after the restart began (typical {RESTART.typical_s + wait.typical_s:.0f} s)")
    # SmartPI resumes at the boiler's start: no pause once the boiler is back, a poll's margin after it.
    settled_after = (back or 0.0) + POLL_S if b.role.boiler_delay_s else 0.0
    found = [f"{NAMES[c]} at {clock(s)}" for s in seen for c in COUNTED if of(s, c, "paused") is True and s["t"] - restarted_at > settled_after]
    b.check(not found, f"no pause after the restart once the boiler is back, from {settled_after:.0f} s ({', '.join(found[:5]) or 'none seen'})")
    if b.role.boiler_delay_s:
        paused = [f"{NAMES[c]} at {clock(s)}, boiler {s['boiler']}" for s in seen for c in COUNTED if of(s, c, "paused") is True]
        b.note(f"pauses after the restart: {', '.join(paused[:6]) or 'none'}")
    snap = await b.after("after the restart")
    for climate in COUNTED:
        now = kept(snap, climate)
        b.check(
            all(now[k] == before[climate][k] for k in ("a", "b", "deadtime")) and all((now[k] or 0) >= (before[climate][k] or 0) for k in ("accepted_a", "accepted_b")),
            f"{NAMES[climate]}: a, b and the dead time kept, {now} (before {before[climate]})",
        )


# --- BP25: a thermostat's own start that completes the threshold -----------------------------
#
# VTherm counts West only once its valve reports the new opening, after seconds with a real valve
# head (the test rooms' report delay). SmartPI pauses only after a calculation period below the
# threshold, so West's own start, which completes the threshold, must never pause. Each start
# begins from the start state; SmartPI logs each pause at info ("West - SmartPI learning paused").

OWN_START_VALVE_DELAY_S = 15.0
OWN_STARTS = 20
OWN_START_WATCH_S = OWN_START_VALVE_DELAY_S + 2 * RECALC_S  # past a calculation period after VTherm counts West
PAUSED_LINE = "West - SmartPI learning paused"


def own_start(role: Role) -> Wait:
    """One start: the start state (the boiler off for the role's delay), East's valve reporting,
    West's start watched."""
    return Wait(
        role.boiler_delay_s + SETTLED_S + OWN_START_VALVE_DELAY_S + OWN_START_WATCH_S,
        START_STATE.maximum_s + OWN_START_VALVE_DELAY_S + WITHIN_S + OWN_START_WATCH_S,
    )


@scenario(
    "BP25",
    ("boiler-count",),
    lambda role: times(own_start(role), OWN_STARTS),
    PAUSE_TESTS,
    north=STOPPED,
    trace=False,
    boiler_at_end=True,
)
async def BP25(b: Bench) -> None:
    """Fresh, the valves reporting their opening 15 s after it is set, East calling alone below the threshold, the boiler off → West starts and completes the threshold, 20 times, each from the start state → West never pauses: SmartPI's log has no "West - SmartPI learning paused" line, though VTherm counts West only once its valve reports."""
    since = datetime.now(UTC)
    done = 0
    try:
        for n in range(1, OWN_STARTS + 1):
            await b.valve_delay(0.0)
            await b.base()
            if not await b.start("fresh", STOPPED):
                continue
            await b.valve_delay(OWN_START_VALVE_DELAY_S)
            await b.setpoints(**{EAST: CALLING})
            took, _ = await b.until(lambda s: (of(s, EAST, "valve") or 0) > 0, OWN_START_VALVE_DELAY_S + WITHIN_S, 2.0)
            if took is None:
                b.note(f"start {n}: East's valve did not report; start skipped")
                continue
            await b.setpoints(**{WEST: CALLING})
            seen = await b.watch(OWN_START_WATCH_S, 1.0)
            paused = [clock(s) for s in seen if of(s, WEST, "paused") is True]
            boiler = next((clock(s) for s in seen if s.get("boiler") == "on"), None)
            b.note(f"start {n}: VTherm's boiler on at {boiler}; West paused in the diagnostics: {', '.join(paused[:3]) or 'never'}")
            done += 1
    finally:
        await b.valve_delay(CONTEXT.valve_delay_s)
    lines = await asyncio.to_thread(instance_log, PAUSED_LINE, since)
    b.check(done == OWN_STARTS, f"{done} of {OWN_STARTS} starts took place")
    b.check(not lines, f"SmartPI's log: {len(lines)} lines {PAUSED_LINE!r}" + (f", the first: {lines[0][:160]}" if lines else ""))


# --- BP26-BP28: a room's own start that waits for the boiler's activation delay ---------------
#
# West's own start completes the threshold; VTherm starts the boiler 120 s later. West pauses
# while the boiler waits and resumes at its start. The pause only pauses learning: SmartPI's
# dead-time estimator then judges the boiler's start as a start of the heating at that moment,
# by its own rules (documentation/en/technical_doc.md, 3.6 and 3.8), among them 600 s without
# heat since the last stop, or since a reset of learning. BP26: from a settled room, the detection
# counts from the boiler's start, not from West's call (the 120 s of waiting are not the room's).
# BP27: West called again shortly after its last heating, so the estimator measures nothing, as
# it would not for a start that did not wait. BP28: the same within 600 s of a reset.

DEADTIME_TRUE_S = (room("West").delay_min + room("West").lag_min) * 60.0  # the radiator's delay and lag
DETECTION = Wait(DEADTIME_TRUE_S + RECALC_S, 3600.0)  # the room's response, then the detection's calculation
DT_WAIT = Wait(3 * RECALC_S, 5 * RECALC_S + POLL_S)  # the detection's waiting time published: 81-180 s on the bench
MIN_OFF_S = 600.0  # deadtime_estimator.py, min_off_time_seconds: the time without heat before a start is measured
# A dead time below one of SmartPI's calculations is a rise that was already under way at the start
# (BP27 on the bench: 0 s after a reset, where the room's is 780 s).
SHORTEST_DEADTIME_S = RECALC_S
SENSOR_STEP_C = 0.1  # the test rooms' sensors
HEAT_S = 5 * 60.0  # BP27, BP28: West heats with the boiler on
REST_S = 5 * 60.0  # BP27: West calls again this long after it stopped
NO_DETECTION_WATCH_S = 15 * 60.0  # BP27: West's diagnostics watched from the boiler's start
CALL_AFTER_RESET_S = 90.0  # BP28: West calls this long after its learning was reset
AFTER_RESET_WATCH_S = 5 * RECALC_S  # BP28: West's diagnostics watched from the boiler's start

SETTLES = Wait(MIN_OFF_S, 3600.0)  # 600 s from a valve's closing and from the reset, then the room's fall


def a_settled_room(climate: str, peak: dict[str, float], reset_at: float | None) -> Callable[[dict[str, Any]], bool]:
    """Holds once the room's valve (or North's switch) has been closed for 600 s and its learning was reset or
    loaded at least 600 s before (``reset_at``), as SmartPI's estimator asks before it measures a
    start, and its room is a sensor step below the highest reading seen meanwhile (``peak``): a
    room still warming from its last heating would rise early and shorten the dead time
    measured."""

    def holds(s: dict[str, Any]) -> bool:
        indoor = of(s, climate, "indoor")
        if not isinstance(indoor, (int, float)):
            return False
        peak["c"] = max(peak.get("c", indoor), indoor)
        closed = published_at(of(s, climate, "valve_since") or of(s, climate, "heater_since"))
        return (
            (of(s, climate, "valve") == 0.0 or of(s, climate, "heater") == "off")
            and closed is not None
            and s["t"] - closed >= MIN_OFF_S
            and s["t"] - (reset_at or 0.0) >= MIN_OFF_S
            and round(peak["c"] - indoor, 2) >= SENSOR_STEP_C
        )

    return holds


async def rooms_settle(b: Bench, climates: Sequence[str]) -> None:
    """Waits until every room in ``climates`` has settled (``a_settled_room``), at once."""
    peaks: dict[str, dict[str, float]] = {c: {} for c in climates}
    tests = {c: a_settled_room(c, peaks[c], b.reset_at.get(c)) for c in climates}
    took, snap = await b.until(lambda s: all([tests[c](s) for c in climates]), SETTLES.maximum_s, 10.0)
    for climate in climates:
        b.check(
            took is not None,
            f"{NAMES[climate]} settled after {seconds(took)} s: its valve closed since {of(snap, climate, 'valve_since')}, "
            f"its learning reset {seconds(snap['t'] - b.reset_at[climate]) if climate in b.reset_at else '—'} s before, "
            f"its room {of(snap, climate, 'indoor')} degC, the highest {peaks[climate].get('c')} degC",
        )


def no_passive_response(b: Bench, seen: Sequence[dict[str, Any]], what: str, climate: str = WEST) -> None:
    """The room's stop armed no detection of its passive response. An armed detection waits at
    least the room's dead time, longer than the watch, so the watch sees it although the
    diagnostics show it up to about 2 min late. (The passive dead time itself cannot change in
    the watch: a detection takes about 1000 s here, and the published value is the median of six.)"""
    armed = [clock(s) for s in seen if of(s, climate, "dt_state") == WAITING_COOL]
    b.check(not armed, f"{what}: no detection of {NAMES[climate]}'s passive response for {len(seen) * POLL_S:.0f} s ({', '.join(armed[:3]) or 'never'})")


async def heat_detection_dropped(b: Bench, climate: str, since: float, off_at: float | None, seen: Sequence[dict[str, Any]]) -> None:
    """The boiler went off at ``off_at`` while ``climate`` called after it had heated: SmartPI's
    log says its heating detection was dropped at that moment, and it waits no more."""
    lines = await asyncio.to_thread(instance_log, HEAT_STOP_LINE, datetime.fromtimestamp(since, UTC))
    stops = [stop for stop in logged_heat_stops(lines, NAMES[climate]) if stop[0] >= since - CLOCK_MARGIN_S]
    first = stops[0] if stops else None
    gap = None if first is None or off_at is None else first[0] - off_at
    b.check(
        first is not None and first[1] and gap is not None and abs(gap) <= AT_ONCE_S,
        f"{NAMES[climate]}'s heating detection dropped as the boiler went off: "
        f"{'no line' if first is None else ('aborted' if first[1] else 'nothing dropped')}, {'—' if gap is None else f'{gap:+.3f}'} s from the boiler's stop",
    )
    waiting = [clock(s) for s in seen if of(s, climate, "dt_state") == WAITING_HEAT]
    b.check(not waiting, f"{NAMES[climate]}'s heating detection waits no more after the boiler's stop ({', '.join(waiting[:3]) or 'never'})")


def at_least_a_calculation(dead: Any) -> bool:
    return isinstance(dead, (int, float)) and dead >= SHORTEST_DEADTIME_S


async def measured_from_the_boiler_s_start(b: Bench) -> None:
    """After West's start that waited for the boiler: the detection armed at the boiler's start
    counts from it, not from West's call (nothing arms at the call: SmartPI's estimator sees no
    power until the boiler starts), and its outcome is detected, with a dead time of at least a
    calculation."""
    snap = await b.snapshot()
    started = published_at(snap.get("boiler_since"))
    took, snap = await b.until(lambda s: of(s, WEST, "dt_state") == WAITING_HEAT and (of(s, WEST, "dt_wait") or 0) > 0, DT_WAIT.maximum_s)
    waited, since_start = of(snap, WEST, "dt_wait"), snap["t"] - (started or snap["t"])
    b.check(
        took is not None and waited is not None and waited <= since_start + CLOCK_MARGIN_S,
        f"the detection counts from the boiler's start: waiting for {seconds(waited)} s, {since_start:.0f} s after the boiler started "
        f"(counted from West's call it would be about {since_start + b.role.boiler_delay_s:.0f} s)",
    )
    took, snap = await b.until(lambda s: of(s, WEST, "dt_state") != WAITING_HEAT, DETECTION.maximum_s, 10.0)
    outcome, dead = of(snap, WEST, "heat_outcome"), of(snap, WEST, "deadtime")
    b.check(outcome == "detected", f"West's heating dead-time detection armed at the boiler's start: {outcome} after {seconds(took)} s")
    b.check(at_least_a_calculation(dead), f"West's heating dead time {dead} s: at least {SHORTEST_DEADTIME_S:.0f} s")
    if isinstance(dead, (int, float)):
        b.note(f"heating dead time {dead:.0f} s: {dead - DEADTIME_TRUE_S:+.0f} s from the room's true {DEADTIME_TRUE_S:.0f} s, {dead - DEADTIME_TRUE_S - b.role.boiler_delay_s:+.0f} s from {DEADTIME_TRUE_S + b.role.boiler_delay_s:.0f} s")


def detection_after_the_boiler_s_start(role: Role) -> Wait:
    """West calls and completes the threshold, the boiler starts after its delay, and the
    detection armed then ends."""
    return total(longest(resume_when_the_boiler_starts(role), boiler_starts(role)), Wait(DETECTION.typical_s, DT_WAIT.maximum_s + DETECTION.maximum_s))


SETTLED_DEPENDS = "typical when the rooms are already 0.1 degC below their highest reading 600 s after their valves closed; after a heating, a room may still rise and then has to cool"


@scenario(
    "BP26",
    ("boiler-delay",),
    lambda role: total(SETTLES, PAUSE_ON_SETPOINT, detection_after_the_boiler_s_start(role)),
    PAUSE_TESTS,
    depends=SETTLED_DEPENDS,
    boiler_at_end=True,
)
async def BP26(b: Bench) -> None:
    """Fresh, an activation delay of 120 s, West without heat and its learning reset at least 600 s before and its room 0.1 degC below its highest reading since, East calling alone below the threshold, the boiler off → West starts and completes the threshold; the boiler starts 120 s later → West pauses while the boiler waits and resumes at its start; the heating dead-time detection armed at the boiler's start counts from it, not from West's call, and ends detected with a dead time of at least 60 s; the dead time is recorded against the room's true 780 s and against 900 s (the true value plus the wait)."""
    await rooms_settle(b, (WEST,))
    await b.change("East calls alone", lambda: b.calls_at(EAST, CALLING), own=EAST)
    await b.west_calls()
    await measured_from_the_boiler_s_start(b)


@scenario(
    "BP27",
    ("boiler-delay",),
    lambda role: total(
        SETTLES,
        PAUSE_ON_SETPOINT,
        longest(resume_when_the_boiler_starts(role), boiler_starts(role)),
        Wait(HEAT_S, HEAT_S),
        Wait(REST_S, max(REST_S, longest(BOILER_STOPS, PAUSE_EXTERNAL).maximum_s)),
        longest(resume_when_the_boiler_starts(role), boiler_starts(role)),
        Wait(NO_DETECTION_WATCH_S, NO_DETECTION_WATCH_S),
    ),
    PAUSE_TESTS,
    depends=SETTLED_DEPENDS,
    boiler_at_end=True,
)
async def BP27(b: Bench) -> None:
    """Fresh, an activation delay of 120 s, West and East without heat and their learning reset at least 600 s before and their rooms 0.1 degC below their highest reading since, East calling alone below the threshold, the boiler off → West calls and heats for 5 minutes with the boiler on, stops, and calls again about 300 s later, completing the threshold; the boiler starts 120 s later → West pauses while the boiler waits and resumes at its start; West was without heat for less than 600 s before the boiler's start (the wait counts), so SmartPI's dead-time estimator measures nothing: for 15 minutes from the boiler's start West's detection never waits for a response, its outcome never turns detected and its heating dead time stays unchanged; East, heated until the boiler went off at West's stop, gets no new measurement either, as the boiler comes back within 600 s of going off, and its heating dead time is none or at least 60 s; West's stop after its 5 minutes of heat arms no detection of its passive response, nor the boiler's stop one of East's, and East's heating detection is dropped as the boiler goes off (SmartPI's log)."""
    await rooms_settle(b, COUNTED)
    await b.change("East calls alone", lambda: b.calls_at(EAST, CALLING), own=EAST)
    await b.west_calls()
    b.note(f"— West heats for {HEAT_S:.0f} s with the boiler on")
    await asyncio.sleep(HEAT_S)
    stopped = time.time()
    await b.west_stops()
    off = await b.snapshot()
    off_at = published_at(off.get("boiler_since")) if off.get("boiler") == "off" else None
    rest = await b.watch(max(0.0, REST_S - (time.time() - stopped)))
    no_passive_response(b, rest, f"West's stop after {HEAT_S / 60:.0f} minutes of heat, before its heating settled")
    no_passive_response(b, rest, f"the boiler's stop after {HEAT_S / 60:.0f} minutes of heat, East fresh", EAST)
    await heat_detection_dropped(b, EAST, stopped, off_at, rest)
    before = await b.snapshot()
    outcome, dead = of(before, WEST, "heat_outcome"), of(before, WEST, "deadtime")
    east_dead = of(before, EAST, "deadtime")
    for climate in COUNTED:
        b.note(
            f"before West calls again: {NAMES[climate]}'s dead-time detection {of(before, climate, 'dt_state')}, "
            f"outcome {of(before, climate, 'heat_outcome')}, heating dead time {of(before, climate, 'deadtime')} s"
        )
    took = await b.west_calls()
    on = took.get("VTherm's boiler on")
    without_heat = None if on is None else b.started + on - stopped
    b.check(
        without_heat is not None and without_heat <= MIN_OFF_S - RECALC_S,
        f"West without heat, and the boiler off, for {seconds(without_heat)} s before the boiler's start, the wait included: less than SmartPI's 600 s",
    )
    seen = await b.watch(NO_DETECTION_WATCH_S, 10.0)
    armed = [clock(s) for s in seen if of(s, WEST, "dt_state") == WAITING_HEAT or (of(s, WEST, "dt_wait") or 0) > 0]
    b.check(not armed, f"for {NO_DETECTION_WATCH_S / 60:.0f} minutes from the boiler's start West's detection never waits for a response ({', '.join(armed[:3]) or 'never'})")
    detected = [clock(s) for s in seen if of(s, WEST, "heat_outcome") == "detected" and outcome != "detected"]
    b.check(not detected, f"West's detection outcome never turns detected: {outcome} before, {sorted({str(of(s, WEST, 'heat_outcome')) for s in seen})} since")
    changed = sorted({of(s, WEST, "deadtime") for s in seen} - {dead}, key=str)
    b.check(not changed, f"West's heating dead time unchanged: {dead} s before, {changed or 'the same'} since")
    armed = [clock(s) for s in seen if of(s, EAST, "dt_state") == WAITING_HEAT]
    b.check(not armed, f"East, heated until the boiler went off: no new detection for {NO_DETECTION_WATCH_S / 60:.0f} minutes from the boiler's return ({', '.join(armed[:3]) or 'never'})")
    changed = sorted({of(s, EAST, "deadtime") for s in seen} - {east_dead}, key=str)
    b.check(not changed, f"East's heating dead time unchanged: {east_dead} s before, {changed or 'the same'} since")
    east = of(seen[-1], EAST, "deadtime") if seen else None
    b.check(east is None or at_least_a_calculation(east), f"East's heating dead time {east} s: none, or at least {SHORTEST_DEADTIME_S:.0f} s")


@scenario(
    "BP28",
    ("boiler-delay",),
    lambda role: total(
        PAUSE_ON_SETPOINT,
        longest(resume_when_the_boiler_starts(role), boiler_starts(role)),
        Wait(HEAT_S, HEAT_S),
        longest(BOILER_STOPS, PAUSE_EXTERNAL),
        Wait(CALL_AFTER_RESET_S, CALL_AFTER_RESET_S),
        longest(resume_when_the_boiler_starts(role), boiler_starts(role)),
        Wait(AFTER_RESET_WATCH_S, AFTER_RESET_WATCH_S),
        longest(BOILER_STOPS, PAUSE_EXTERNAL),
        SETTLES,
        detection_after_the_boiler_s_start(role),
    ),
    PAUSE_TESTS,
    depends=SETTLED_DEPENDS,
    boiler_at_end=True,
)
async def BP28(b: Bench) -> None:
    """Fresh, an activation delay of 120 s, East calling alone below the threshold; West heats for 5 minutes with the boiler on and stops, and its learning is reset right after, its room still warming → West calls about 90 s after the reset and waits for the boiler; then West stops, settles (600 s from the reset and from its valve closing, its room 0.1 degC below its highest reading) and calls again → within 600 s of the reset SmartPI measures nothing: for 5 minutes from the boiler's start West's detection never waits for a response, and it has no outcome and no heating dead time, and West's stop after that heating arms no detection of its passive response; the settled start's detection, armed at the boiler's start, ends detected with a dead time of at least 60 s."""
    await b.change("East calls alone", lambda: b.calls_at(EAST, CALLING), own=EAST)
    await b.west_calls()
    b.note(f"— West heats for {HEAT_S:.0f} s with the boiler on")
    await asyncio.sleep(HEAT_S)
    await b.west_stops()
    at_reset = await b.snapshot()
    await b.reset(WEST)
    took, snap = await b.until(lambda s: of(s, WEST, "deadtime_reliable") is False and of(s, WEST, "heat_outcome") is None, MARGIN_S)
    b.check(took is not None, f"West's learning reset: heating dead time {of(snap, WEST, 'deadtime')} s, outcome {of(snap, WEST, 'heat_outcome')}")
    await asyncio.sleep(max(0.0, CALL_AFTER_RESET_S - (time.time() - b.reset_at[WEST])))
    b.note(f"West's room {of(at_reset, WEST, 'indoor')} degC at the reset, {of(await b.snapshot(), WEST, 'indoor')} degC {time.time() - b.reset_at[WEST]:.0f} s later")
    took = await b.west_calls()
    on = took.get("VTherm's boiler on")
    after_reset = None if on is None else b.started + on - b.reset_at[WEST]
    b.check(
        after_reset is not None and after_reset <= MIN_OFF_S - RECALC_S,
        f"the boiler started {seconds(after_reset)} s after West's reset: less than SmartPI's 600 s",
    )
    seen = await b.watch(AFTER_RESET_WATCH_S, 10.0)
    armed = [clock(s) for s in seen if of(s, WEST, "dt_state") == WAITING_HEAT or (of(s, WEST, "dt_wait") or 0) > 0]
    b.check(not armed, f"for {AFTER_RESET_WATCH_S / 60:.0f} minutes from the boiler's start West's detection never waits for a response ({', '.join(armed[:3]) or 'never'})")
    learned = sorted({f"{of(s, WEST, 'heat_outcome')}, {of(s, WEST, 'deadtime')} s" for s in seen})
    b.check(learned == ["None, None s"], f"no measurement within 600 s of the reset: outcome and heating dead time {learned}")
    await b.west_stops()
    rest = await b.watch(AFTER_THE_STOP.maximum_s)
    no_passive_response(b, rest, "West's stop after its heating since the reset, without a reliable heating dead time")
    await rooms_settle(b, (WEST,))
    await b.west_calls()
    await measured_from_the_boiler_s_start(b)


# --- BP29, BP30: the passive response after a settled heating --------------------------------
#
# SmartPI times a room's passive response after a stop only once the heating before it has
# settled: it lasted the heating dead time and, with a published response lag, its settling time
# (three lags, at most half the median heating period), the gate of an active learning window
# (documentation/en/technical_doc.md, 3.6). Without a reliable heating dead time it times none.

SETTLED_STATE = "west_settled"  # learned_state.SETTLED: West with its response lag published, 780 s
SETTLED_HEAT_S = 45 * 60.0  # BP29: more than West's settling time, three lags of 780 s (39 min)
LONG_HEAT_S = 30 * 60.0  # BP30
# BP29, BP31: the room still warms after the stop (the radiator's 10-minute lag), then falls by
# 0.05 degC: 1275 s from the stop in BP29 on the bench (check of 11e3071).
PASSIVE_DETECTION = Wait(1275.0, 3600.0)
# SmartPI counts a heating from its committed power, up to a cycle after the valve opens (about
# 3 min in BP29 on the bench): a heating that must count more than a time lasts a cycle more.
COUNTED_LATE_S = CYCLE_S
# BP29, BP31: from West's stop, its 0 % is committed at SmartPI's next calculation, which arms the
# detection, and the diagnostics show it up to PUBLISHED_LATE_S later (124 s in BP31 on the bench).
ARMED_WITHIN_S = RECALC_S + PUBLISHED_LATE_S + MARGIN_S
SHORT_HEAT_S = 5 * 60.0  # BP31: shorter than West's heating dead time, 780 s
LEARNED_HEAT_S = 20 * 60.0  # BP31: longer than 780 s even counted a cycle late
BETWEEN_HEAT_S = 25 * 60.0  # BP32: between 780 s and the settling time of 2340 s, counted a cycle early or late


def settling_s(dead: Any, lag: Any, heating_median: Any) -> float | None:
    """The heating a passive response needs before it is timed, as SmartPI computes it
    (deadtime_estimator.py, _active_settling_s): the heating dead time and, with a published
    response lag, at least min(3 lags, half the median heating period)."""
    if not isinstance(dead, (int, float)):
        return None
    if not isinstance(lag, (int, float)):
        return float(dead)
    settle = 3.0 * lag if not isinstance(heating_median, (int, float)) else min(3.0 * lag, heating_median / 2.0)
    return max(float(dead), settle)


async def room_s_settling(b: Bench, lag_published: bool, climate: str = WEST) -> float | None:
    """The room's heating dead time is reliable and its response lag published, or not, as the
    scenario needs; the settling time SmartPI then applies."""
    snap = await b.snapshot()
    dead, lag, median = of(snap, climate, "deadtime"), of(snap, climate, "lag"), of(snap, climate, "heating_median")
    settling = settling_s(dead, lag, median)
    b.check(
        of(snap, climate, "deadtime_reliable") is True and (lag is not None) is lag_published,
        f"{NAMES[climate]}: heating dead time {dead} s (reliable {of(snap, climate, 'deadtime_reliable')}), response lag {lag} s, "
        f"median heating period {median} s: SmartPI times a passive response after {seconds(settling)} s of heat",
    )
    return settling


async def passive_detection_shown(b: Bench, climate: str, since: float, what: str) -> dict[str, Any]:
    """The room's passive detection shows in its diagnostics within ``ARMED_WITHIN_S`` of
    ``since`` (its stop, or the boiler's); the snapshot that shows it."""
    took, snap = await b.until(lambda s: of(s, climate, "dt_state") == WAITING_COOL, max(0.0, ARMED_WITHIN_S - (time.time() - since)))
    b.check(
        took is not None,
        f"{what} arms a detection of {NAMES[climate]}'s passive response, shown {seconds(snap['t'] - since)} s after it "
        f"(within {ARMED_WITHIN_S:.0f} s): {of(snap, climate, 'dt_state')}",
    )
    return snap


async def passive_detections_end_detected(b: Bench, climates: Sequence[str], since: float) -> None:
    """Each room's passive detection goes on without a break and ends detected: the first reading
    out of WAITING_COOL_RESPONSE shows the outcome "detected", not "aborted". SmartPI's log, whose
    lines name no room, gives at least one passive dead time per room since ``since``, each of at
    least 60 s."""
    ended: dict[str, dict[str, Any]] = {}
    start = time.time()
    while True:
        snap = await b.snapshot()
        for climate in climates:
            if climate not in ended and of(snap, climate, "dt_state") != WAITING_COOL:
                ended[climate] = snap
        if len(ended) == len(climates) or time.time() - start > PASSIVE_DETECTION.maximum_s:
            break
        await asyncio.sleep(10.0)
    for climate in climates:
        snap = ended.get(climate)
        outcome = None if snap is None else of(snap, climate, "cool_outcome")
        b.check(
            outcome == "detected",
            f"{NAMES[climate]}'s passive detection went on until it ended: {outcome}"
            + ("" if snap is None else f", {snap['t'] - since:.0f} s after the stop"),
        )
    lines = await asyncio.to_thread(instance_log, COOL_DETECTED_LINE, datetime.fromtimestamp(since, UTC))
    found = [value for at, value in logged_cool_dead_times(lines) if at >= since - CLOCK_MARGIN_S]
    b.check(
        len(found) >= len(climates) and all(value >= SHORTEST_DEADTIME_S for value in found),
        f"passive dead times in SmartPI's log since the stop: {', '.join(f'{v:.1f}' for v in found) or 'none'} s, "
        f"at least {len(climates)}, each at least {SHORTEST_DEADTIME_S:.0f} s",
    )


async def passive_response_measured(b: Bench, heated_s: float) -> None:
    """West stops after ``heated_s`` of heat, longer than its settling time: SmartPI arms a
    detection of its passive response, it ends detected, and the log gives at least 60 s."""
    stopped = time.time()
    await b.west_stops()
    await passive_detection_shown(b, WEST, stopped, f"West's stop after {heated_s / 60:.0f} minutes of heat")
    await passive_detections_end_detected(b, (WEST,), stopped)


async def both_call(b: Bench) -> None:
    await b.calls_at(WEST, CALLING)
    await b.calls_at(EAST, CALLING)


@scenario(
    "BP29",
    ("boiler-count",),
    lambda role: total(boiler_starts(role), Wait(SETTLED_HEAT_S, SETTLED_HEAT_S), longest(BOILER_STOPS, PAUSE_EXTERNAL), PASSIVE_DETECTION),
    PAUSE_TESTS,
    depends="the passive response's time, estimated: how long the room keeps warming after the stop",
    start="learned",
    north=STOPPED,
    boiler_at_end=True,
)
async def BP29(b: Bench) -> None:
    """West learned with its response lag published (780 s, heating periods of 2 h), East learned, North off → West and East call, the boiler on, and West heats for 45 minutes, longer than its settling time of three lags (39 min), then stops, which turns the boiler off while East still calls; East stops only once its passive detection shows → SmartPI arms a detection of West's passive response at West's stop and one of East's at the boiler's stop (East, heated 45 minutes, still calls then, so it is the boiler's stop that arms it); both go on without a break and end detected, and SmartPI's log gives passive dead times of at least 60 s."""
    await b.load(WEST, SETTLED_STATE)
    settling = await room_s_settling(b, lag_published=True)
    b.check(settling is not None and SETTLED_HEAT_S >= settling + COUNTED_LATE_S, f"{SETTLED_HEAT_S / 60:.0f} minutes of heat count more than {seconds(settling)} s, a cycle late")
    await b.change("West and East call", lambda: both_call(b))
    b.note(f"— West heats for {SETTLED_HEAT_S / 60:.0f} minutes with the boiler on")
    await asyncio.sleep(SETTLED_HEAT_S)
    stopped = time.time()
    await b.west_stops()
    off = await b.snapshot()
    off_at = published_at(off.get("boiler_since")) if off.get("boiler") == "off" else None
    b.check(off_at is not None, f"West's stop turns VTherm's boiler off: {off.get('boiler')} since {off.get('boiler_since')}")
    await passive_detection_shown(b, WEST, stopped, f"West's stop after {SETTLED_HEAT_S / 60:.0f} minutes of heat")
    # East still calls: its detection can only be armed by the boiler's stop.
    snap = await passive_detection_shown(b, EAST, off_at or stopped, f"The boiler's stop after {SETTLED_HEAT_S / 60:.0f} minutes of East's heat")
    b.check(calls(snap, EAST), f"East still calls as its passive detection shows: valve opening {of(snap, EAST, 'opening')} %")
    await b.east_stops()
    await passive_detections_end_detected(b, (WEST, EAST), stopped)


@scenario(
    "BP30",
    ("boiler-count",),
    lambda role: total(boiler_starts(role), Wait(LONG_HEAT_S, LONG_HEAT_S), longest(BOILER_STOPS, PAUSE_EXTERNAL), AFTER_THE_STOP),
    PAUSE_TESTS,
    north=STOPPED,
    boiler_at_end=True,
)
async def BP30(b: Bench) -> None:
    """Fresh, North off → West and East call right after the reset, so SmartPI measures no heating dead time (600 s after a reset); the boiler on, West heats for 30 minutes and stops → without a reliable heating dead time SmartPI arms no detection of West's passive response: for five calculations no WAITING_COOL_RESPONSE and no passive outcome."""
    await b.change("West and East call", lambda: both_call(b))
    after_reset = b.started - b.reset_at[WEST]
    b.check(after_reset <= MIN_OFF_S - RECALC_S, f"West calls {after_reset:.0f} s after its reset: SmartPI measures no heating dead time from this start")
    b.note(f"— West heats for {LONG_HEAT_S / 60:.0f} minutes with the boiler on")
    await asyncio.sleep(LONG_HEAT_S)
    snap = await b.snapshot()
    b.check(of(snap, WEST, "deadtime_reliable") is False, f"West's heating dead time not reliable at its stop: {of(snap, WEST, 'deadtime')} s")
    outcome_before = of(snap, WEST, "cool_outcome")
    await b.west_stops()
    seen = await b.watch(AFTER_THE_STOP.maximum_s)
    no_passive_response(b, seen, f"West's stop after {LONG_HEAT_S / 60:.0f} minutes of heat without a reliable heating dead time")
    outcomes = sorted({str(of(s, WEST, "cool_outcome")) for s in seen})
    b.check(outcomes == [str(outcome_before)], f"West's passive outcome {outcome_before} before its stop, {outcomes} since")


# --- BP31, BP32: a heating too short for SmartPI's settling time ---------------------------------
#
# The rule's two other branches: a reliable heating dead time with a heating shorter than it (BP31,
# then a heating longer than it, measured), and a published response lag with a heating between
# the dead time and the settling time (BP32). On the device count, North off: only West stops.


@scenario(
    "BP31",
    ("boiler-count",),
    lambda role: total(
        boiler_starts(role),
        Wait(SHORT_HEAT_S, SHORT_HEAT_S),
        Wait(MIN_OFF_S, MIN_OFF_S + longest(BOILER_STOPS, PAUSE_EXTERNAL).maximum_s),
        boiler_starts(role),
        Wait(LEARNED_HEAT_S, LEARNED_HEAT_S),
        longest(BOILER_STOPS, PAUSE_EXTERNAL),
        PASSIVE_DETECTION,
    ),
    PAUSE_TESTS,
    depends="the passive response's time: how long the room keeps warming after the stop",
    start="learned",
    north=STOPPED,
    boiler_at_end=True,
)
async def BP31(b: Bench) -> None:
    """West learned (heating dead time 780 s, no response lag published), East learned, North off → West and East call, the boiler on; West heats for 5 minutes, shorter than its dead time, and stops; after 600 s without heat it calls again, heats for 20 minutes and stops → after the short heating SmartPI arms no detection of West's passive response for 300 s, nor the boiler's stop one of East's (learned, heated 5 minutes as well); after the long one the detection arms and ends detected, and SmartPI's log gives a passive dead time of at least 60 s."""
    settling = await room_s_settling(b, lag_published=False)
    b.check(
        settling is not None and SHORT_HEAT_S < settling <= LEARNED_HEAT_S - COUNTED_LATE_S,
        f"{SHORT_HEAT_S / 60:.0f} minutes of heat count less than {seconds(settling)} s, {LEARNED_HEAT_S / 60:.0f} minutes more, a cycle late",
    )
    await b.change("West and East call", lambda: both_call(b))
    b.note(f"— West heats for {SHORT_HEAT_S / 60:.0f} minutes with the boiler on")
    await asyncio.sleep(SHORT_HEAT_S)
    stopped = time.time()
    await b.west_stops()
    rest = await b.watch(AFTER_THE_STOP.maximum_s)
    no_passive_response(b, rest, f"West's stop after {SHORT_HEAT_S / 60:.0f} minutes of heat, shorter than its heating dead time")
    no_passive_response(b, rest, f"the boiler's stop after {SHORT_HEAT_S / 60:.0f} minutes of East's heat, shorter than its heating dead time", EAST)
    await asyncio.sleep(max(0.0, MIN_OFF_S - (time.time() - stopped)))
    await b.west_calls()
    b.note(f"— West heats for {LEARNED_HEAT_S / 60:.0f} minutes with the boiler on")
    await asyncio.sleep(max(0.0, LEARNED_HEAT_S - (time.time() - b.started)))
    await passive_response_measured(b, LEARNED_HEAT_S)


@scenario(
    "BP32",
    ("boiler-count",),
    lambda role: total(boiler_starts(role), Wait(BETWEEN_HEAT_S, BETWEEN_HEAT_S), longest(BOILER_STOPS, PAUSE_EXTERNAL), AFTER_THE_STOP),
    PAUSE_TESTS,
    start="learned",
    north=STOPPED,
    boiler_at_end=True,
)
async def BP32(b: Bench) -> None:
    """West learned with its response lag published (780 s, heating periods of 2 h, so a settling time of 2340 s), East learned, North off → West and East call, the boiler on; West heats for 25 minutes, longer than its dead time but shorter than its settling time, and stops → SmartPI arms no detection of West's passive response for 300 s."""
    await b.load(WEST, SETTLED_STATE)
    settling = await room_s_settling(b, lag_published=True)
    b.check(
        settling is not None and BETWEEN_HEAT_S + COUNTED_LATE_S < settling,
        f"{BETWEEN_HEAT_S / 60:.0f} minutes of heat count less than {seconds(settling)} s, even a cycle long",
    )
    await b.change("West and East call", lambda: both_call(b))
    b.note(f"— West heats for {BETWEEN_HEAT_S / 60:.0f} minutes with the boiler on")
    await asyncio.sleep(BETWEEN_HEAT_S)
    await b.west_stops()
    rest = await b.watch(AFTER_THE_STOP.maximum_s)
    no_passive_response(b, rest, f"West's stop after {BETWEEN_HEAT_S / 60:.0f} minutes of heat, longer than its dead time, shorter than its settling time")


# --- BP33: a room's own stop before its pause, and a new call, after the boiler's stop -----------
#
# The boiler stops heating East while East still calls; East stops itself about 30 s later,
# before its learning pause (a calculation period, 59 s), and calls again two calculations later,
# the boiler still off (East alone stays below the threshold of 2 devices). SmartPI's dead-time
# estimator sees no power while the boiler is off, so it times East's passive response from the
# boiler's stop, and neither East's own stop nor its new call changes anything
# (documentation/en/technical_doc.md, 3.8). West does not call: the log's dead time lines, which
# name no room, are East's alone.

CUT_HEAT_S = 20 * 60.0  # East heats longer than its dead time (780 s), even counted a cycle late
STOP_AFTER_CUT_S = 30.0  # East stops this long after the boiler's stop
STOP_AFTER_CUT_MIN_S = 20.0  # at least this long: a start from East's stop would show 20 s or more late
CALL_AGAIN_AFTER_STOP_S = 2 * RECALC_S  # East calls again this long after its stop, the boiler still off
# The detection's start from its log line against the boiler's stop: the dead time and the drop's
# delay are logged to 0.1 s each; the start is the moment the handler saw the boiler's state
# change, and the line's time the calculation's, both on Home Assistant's clock, which itself
# stamps the boiler's change: they differ by milliseconds. 1 s keeps a wide margin and stays far
# below the 20 s a start from East's own stop would show.
START_AT_THE_CUT_S = 1.0


async def passive_detection_goes_on(b: Bench, climate: str, since: float, called_at: float, prior: Any) -> None:
    """The room's passive detection, armed at ``since`` (the boiler's stop), goes on without a break
    through the room's new call at ``called_at`` and ends detected: every reading up to its end
    shows it waiting, its age never falls (a new detection would start from 0 again) and reaches past
    the call; the room never waits for a heating response, and the boiler stays off. ``prior`` is
    the room's outcome before the run: when it was already "detected", the outcome alone proves
    nothing, and the log's line decides (BP33)."""
    ages: list[tuple[float, float]] = []
    heat: list[str] = []
    boiler: list[str] = []
    start = time.time()
    while True:
        snap = await b.snapshot()
        state = of(snap, climate, "dt_state")
        if state == WAITING_HEAT:
            heat.append(clock(snap))
        if snap.get("boiler") != "off":
            boiler.append(f"{clock(snap)} {snap.get('boiler')}")
        if state != WAITING_COOL or time.time() - start > PASSIVE_DETECTION.maximum_s:
            break
        age = of(snap, climate, "dt_wait")
        if isinstance(age, (int, float)):
            ages.append((snap["t"], float(age)))
        await asyncio.sleep(10.0)
    falls = [f"{a:.0f} s to {b_:.0f} s" for (_, a), (_, b_) in pairwise(ages) if b_ < a]
    spans = ages[-1][1] if ages else None
    b.check(
        not falls and spans is not None and spans >= called_at - since - CLOCK_MARGIN_S,
        f"{NAMES[climate]}'s passive detection went on through its new call ({called_at - since:.0f} s after the boiler's stop): "
        f"its age reached {seconds(spans)} s and never fell ({', '.join(falls[:3]) or 'no fall'})",
    )
    outcome = of(snap, climate, "cool_outcome")
    b.check(
        outcome == "detected",
        f"{NAMES[climate]}'s passive detection ended: {outcome}, {snap['t'] - since:.0f} s after the boiler's stop"
        + (" (already detected before the run: the log's line decides)" if prior == "detected" else ""),
    )
    b.check(not heat, f"{NAMES[climate]} never waits for a heating response while the boiler is off ({', '.join(heat[:3]) or 'never'})")
    b.check(not boiler, f"VTherm's boiler stays off meanwhile ({', '.join(boiler[:3]) or 'off throughout'})")


@scenario(
    "BP33",
    ("boiler-count",),
    lambda role: total(PUBLISHED, boiler_starts(role), Wait(CUT_HEAT_S, CUT_HEAT_S), BOILER_STOPS, Wait(STOP_AFTER_CUT_S, STOP_AFTER_CUT_S + MARGIN_S), PASSIVE_DETECTION, PUBLISHED),
    PAUSE_TESTS,
    depends="the passive response's time: how long the room keeps warming after the boiler's stop",
    start="learned",
    north=STOPPED,
    boiler_at_end=True,
)
async def BP33(b: Bench) -> None:
    """East learned (heating dead time 780 s, no response lag published), West and North not calling → VTherm's threshold lowered to 1 device, East calls and heats 20 minutes with the boiler on; the threshold back to 2 turns the boiler off while East calls, East stops itself 20-59 s later, before its learning pause, and calls again 120 s after its stop, the boiler still off (East alone is below the threshold) → SmartPI times East's passive response from the boiler's stop, and neither East's own stop nor its new call changes it: exactly one "Cool Deadtime detected" line after the boiler's stop, of at least 60 s, whose start lies within 1 s of the boiler's stop; the detection goes on without a break through the new call (its age never falls) and ends detected; East never waits for a heating response and the log has no "Heat Deadtime detected" line while the boiler stays off; the threshold is back to 2."""
    settling = await room_s_settling(b, lag_published=False, climate=EAST)
    b.check(
        settling is not None and CUT_HEAT_S - COUNTED_LATE_S >= settling,
        f"{CUT_HEAT_S / 60:.0f} minutes of heat count more than {seconds(settling)} s, a cycle late",
    )
    prior = of(await b.snapshot(), EAST, "cool_outcome")
    b.note(f"East's passive outcome before the run: {prior}")
    entity_id, original = await b.threshold_entity()
    try:
        await b.threshold_set(1.0)
        await b.east_calls()
        b.note(f"— East heats for {CUT_HEAT_S / 60:.0f} minutes with the boiler on")
        await asyncio.sleep(max(0.0, CUT_HEAT_S - (time.time() - b.started)))
        b.note(f"— VTherm's threshold back to {original:g}, East calling")
        await b.api.call("number", "set_value", {"entity_id": entity_id, "value": original})
        b.count_threshold = original
        took, snap = await b.until(lambda s: s["boiler"] == "off", BOILER_STOPS.maximum_s, 1.0)
        off_at = published_at(snap.get("boiler_since")) if took is not None else None
        b.check(off_at is not None, f"VTherm's boiler off while East calls: {snap.get('boiler')} since {snap.get('boiler_since')}")
        if off_at is None:
            return
        await asyncio.sleep(max(0.0, off_at + STOP_AFTER_CUT_S - time.time()))
        before = await b.snapshot()
        b.check(of(before, EAST, "paused") is False and calls(before, EAST), f"East still calls and is not paused before its stop: paused {of(before, EAST, 'paused')}, valve opening {of(before, EAST, 'opening')} %")
        b.note("— East stops calling, before its pause")
        await b.calls_at(EAST, STOPPED)
        stopped_at = time.time()
        await b.after("East stopped after the boiler's stop")

        since = datetime.fromtimestamp(off_at, UTC)
        changes = [at for at, _, new in logged_target_changes(await asyncio.to_thread(instance_log, TARGET_CHANGE_LINE, since), NAMES[EAST]) if at >= off_at and new == STOPPED]
        gap = changes[0] - off_at if changes else None
        b.check(
            gap is not None and STOP_AFTER_CUT_MIN_S <= gap < PAUSE_CONFIRM_S,
            f"East stopped {seconds(gap)} s after the boiler's stop (SmartPI's log): at least {STOP_AFTER_CUT_MIN_S:.0f} s, less than {PAUSE_CONFIRM_S:.0f} s",
        )
        pauses = [at for at, paused in logged_pauses(await asyncio.to_thread(instance_log, BOILER_PAUSE_LINE, since), NAMES[EAST]) if paused and off_at <= at <= (changes[0] if changes else off_at + PAUSE_CONFIRM_S)]
        b.check(not pauses, f"East not paused between the boiler's stop and its own (SmartPI's log): {len(pauses)} pause lines")

        await passive_detection_shown(b, EAST, off_at, "The boiler's stop after 20 minutes of East's heat")
        await asyncio.sleep(max(0.0, stopped_at + CALL_AGAIN_AFTER_STOP_S - time.time()))
        b.note("— East calls again, the boiler still off")
        await b.calls_at(EAST, CALLING)
        await passive_detection_goes_on(b, EAST, off_at, time.time(), prior)
        b.note("— East stops calling")
        await b.calls_at(EAST, STOPPED)

        lines = await asyncio.to_thread(instance_log, COOL_DETECTED_LINE, since)
        starts = [(value, start) for at, value, start in logged_cool_starts(lines) if at >= off_at - CLOCK_MARGIN_S]
        b.check(
            len(starts) == 1 and abs(starts[0][1] - off_at) <= START_AT_THE_CUT_S and starts[0][0] >= SHORTEST_DEADTIME_S,
            "one passive dead time after the boiler's stop, of at least "
            + f"{SHORTEST_DEADTIME_S:.0f} s, started at it: "
            + (", ".join(f"{value:.1f} s from {start - off_at:+.1f} s" for value, start in starts) or "none")
            + f" (within {START_AT_THE_CUT_S:.0f} s; East's own stop came {seconds(gap)} s after the boiler's)",
        )
        heat_lines = await asyncio.to_thread(instance_log, HEAT_DETECTED_LINE, since)
        b.check(not heat_lines, f"no heating dead time in SmartPI's log while the boiler stays off: {len(heat_lines)} lines" + (f", the first: {heat_lines[0][:160]}" if heat_lines else ""))
    finally:
        await b.api.call("number", "set_value", {"entity_id": entity_id, "value": original})
        b.count_threshold = original
        snap = await b.snapshot()
        b.check(snap.get("count_threshold") == original, f"VTherm's threshold back to {original:g}: {snap.get('count_threshold')}")


# --- BP34, BP35: a room behind a relay that alone keeps the boiler on --------------------------
#
# boiler-relay: North is a water radiator behind an on/off actuator, a relay that VTherm drives as
# a switch, and the central boiler counts it with a threshold of one device. SmartPI drives a
# switch in cycles: at a command of 60 % the relay is on for 60 % of each cycle and off for the
# rest, a pulse whose width follows the controller's output (Astrom and Hagglund 1995, 3.4,
# p. 78-79, equation 3.23). The off part of the cycle is SmartPI's own command, not a lack of heat.
# VTherm counts a switch only while it is on, so with North alone the boiler goes off in each off
# part. BP34 and BP35 expect what SmartPI's documentation promises (technical_doc.md 3.6 and 3.8):
# no learning pause and no stop of heat in the room's own off part. The plugin of qas a6d0f78 does
# not keep it (3.8, limits): both scenarios are written to show that, and to pass once it is fixed.

RELAY_STATE = "north_relay"  # learned_state.RELAY: North of boiler-relay, its room's model learned
HOLD_STEP_C = 1.0  # BP34: North's setpoint this far above its room: 50-65 % in the local simulation
RELAY_WATCH_S = 6 * CYCLE_S  # BP34: six of North's cycles
RELAY_BOILER_OFFS = 3  # BP34: the boiler off at least this often while North calls, or the role shows nothing
HEAT_STEP_C = 2.0  # BP35: 100 % for about 40 minutes in the local simulation, then SmartPI modulates
MODULATES = Wait(2400.0, 5400.0)  # BP35: from the step until North's command falls below 100 %
MODULATED_S = 2 * CYCLE_S  # BP35: North heats two cycles more, modulating, before it stops


async def north_learned(b: Bench) -> None:
    """North given its room's learned model (the relay room of boiler-relay)."""
    await b.load(NORTH, RELAY_STATE)


async def north_heat_stops(since: float, until: float) -> list[float]:
    """North's "heat stopped with the central boiler" lines from ``since`` to ``until``."""
    lines = await asyncio.to_thread(instance_log, HEAT_STOP_LINE, datetime.fromtimestamp(since, UTC))
    return [at for at, _ in logged_heat_stops(lines, NAMES[NORTH]) if since - CLOCK_MARGIN_S <= at <= until]


@scenario(
    "BP34",
    ("boiler-relay",),
    lambda role: total(PUBLISHED, Wait(RELAY_WATCH_S, RELAY_WATCH_S)),
    PAUSE_TESTS,
    north=STOPPED,
    boiler_at_end=True,
)
async def BP34(b: Bench) -> None:
    """North learned (a water radiator behind a relay, the boiler's threshold one device), West and East not calling → North's setpoint 1 degC above its room, so that SmartPI modulates: the relay is on for part of each 5-minute cycle and off for the rest, and VTherm's boiler, kept on by North alone, goes off in each off part → the role shows the case: the boiler goes off at least 3 times in 30 minutes while North's command stays above 0; North's own off part is not a lack of heat: North never pauses (diagnostics and SmartPI's log) and SmartPI logs no "North - SmartPI heat stopped with the central boiler" line."""
    await north_learned(b)
    room_c = of(await b.snapshot(), NORTH, "indoor")
    if not isinstance(room_c, (int, float)):
        b.check(False, f"North's room temperature: {room_c}")
        return
    target = round(room_c + HOLD_STEP_C, 1)
    b.note(f"— North's setpoint {target:g} degC, its room {room_c:g} degC")
    since = time.time()
    await b.calls_at(NORTH, target)
    seen = await b.watch(RELAY_WATCH_S)
    await b.calls_at(NORTH, STOPPED)

    openings = [of(s, NORTH, "opening") for s in seen if isinstance(of(s, NORTH, "opening"), (int, float))]
    calling_offs = [s for prev, s in pairwise(seen) if prev["boiler"] == "on" and s["boiler"] == "off" and (of(s, NORTH, "opening") or 0) > 0]
    b.check(
        len(calling_offs) >= RELAY_BOILER_OFFS,
        f"VTherm's boiler went off {len(calling_offs)} times while North called (at least {RELAY_BOILER_OFFS}); "
        f"North's command {min(openings, default=None)}-{max(openings, default=None)} %",
    )
    paused = [clock(s) for s in seen if of(s, NORTH, "paused") is True]
    b.check(not paused, f"North never paused in its own cycle ({len(paused)} readings paused, the first {', '.join(paused[:3]) or 'none'})")
    lines = await asyncio.to_thread(instance_log, BOILER_PAUSE_LINE, datetime.fromtimestamp(since, UTC))
    pauses = [at for at, is_paused in logged_pauses(lines, NAMES[NORTH]) if is_paused]
    b.check(not pauses, f"SmartPI's log: {len(pauses)} \"North - SmartPI learning paused\" lines")
    stops = await north_heat_stops(since, time.time())
    b.check(not stops, f"SmartPI's log: {len(stops)} \"North - SmartPI heat stopped with the central boiler\" lines while North called")


@scenario(
    "BP35",
    ("boiler-relay",),
    lambda role: total(PUBLISHED, SETTLES, MODULATES, Wait(MODULATED_S, MODULATED_S), PASSIVE_DETECTION),
    PAUSE_TESTS,
    depends="when SmartPI starts to modulate, and the passive response's time",
    north=STOPPED,
    boiler_at_end=True,
)
async def BP35(b: Bench) -> None:
    """North learned (a water radiator behind a relay, the boiler's threshold one device), West and East not calling; North settled, 600 s after its learned state was loaded and its room not warming → North's setpoint 2 degC above its room: SmartPI heats at 100 %, then modulates, the relay off in part of each cycle and VTherm's boiler with it; North heats two cycles more and stops → North's heating counts from the step, not from the relay's last start: no "North - SmartPI heat stopped with the central boiler" line while North called; North's stop arms a detection of its passive response, shown within 495 s, which goes on without a break and ends detected with at least 60 s."""
    await north_learned(b)
    await rooms_settle(b, (NORTH,))
    room_c = of(await b.snapshot(), NORTH, "indoor")
    if not isinstance(room_c, (int, float)):
        b.check(False, f"North's room temperature: {room_c}")
        return
    target = round(room_c + HEAT_STEP_C, 1)
    b.note(f"— North's setpoint {target:g} degC, its room {room_c:g} degC")
    since = time.time()
    await b.calls_at(NORTH, target)
    took, snap = await b.until(lambda s: 0 < (of(s, NORTH, "opening") or 0) < 100, MODULATES.maximum_s, 10.0)
    b.check(took is not None, f"North modulates {seconds(took)} s after the step: command {of(snap, NORTH, 'opening')} %, heating outcome {of(snap, NORTH, 'heat_outcome')}")
    if took is None:
        await b.calls_at(NORTH, STOPPED)
        return
    b.note(f"— North heats {MODULATED_S / 60:.0f} minutes more, modulating")
    await asyncio.sleep(MODULATED_S)
    b.note("— North stops calling")
    await b.calls_at(NORTH, STOPPED)
    stopped = time.time()
    stops = await north_heat_stops(since, stopped)
    b.check(not stops, f"SmartPI's log: {len(stops)} \"North - SmartPI heat stopped with the central boiler\" lines while North called")
    await passive_detection_shown(b, NORTH, stopped, f"North's stop after {(stopped - since) / 60:.0f} minutes of heat")
    await passive_detections_end_detected(b, (NORTH,), stopped)

