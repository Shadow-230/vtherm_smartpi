"""The QAS scenarios: SmartPI's learning pause below Versatile Thermostat's central boiler
threshold (documentation/en/technical_doc.md, 3.8), on a prepared instance.

West and East heat with water radiators the central boiler counts; North with an electric heater
it does not count. What a scenario shares with the others, the start state, the steps and their
checks, the timing and the trace, is in ``steps.py``.

Each scenario is registered with its roles, how its learning starts (``fresh`` or ``learned``),
North's setpoint, its time, whether it is in the minimum set, whether it passes on its checks or
on a measurement, and its counterpart without the bench; the catalogue in README.md lists the
same (checked by tests/test_qas_files.py). Its docstring says it in one line: the start state →
the event → the expected result. A scenario's time is the sum of its waits (``Wait``), for each
of its roles: "typical" is what SmartPI's timing gives, "maximum" the sum of the waits' limits.
Every wait ends as soon as its condition holds.

A scenario that passes on a measurement passes only once the measurement took place: each says
what that is (BP12, BP14–BP16, BP19, BP21, BR2).

BR1 and BR2 are the reference runs: the same case on this version and on the author's release
(``pack.AUTHOR_RELEASE``), which has no learning pause. On it the runner runs with
``--author-release``: only BP0, BP14, BR1 and BR2, and they do not look for
``learning.boiler_paused``.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pack import AUTHOR_RELEASE, ROLES, SMARTPI, VT, Role, room
from steps import (
    AFTER_RESTART,
    BASE_SETPOINT,
    BOILER_DELAY_S,
    BOILER_STOPS,
    CALLING,
    CYCLE_S,
    COUNTED,
    DETACHED_AFTER_S,
    EAST,
    HOLD,
    KEYS,
    MARGIN_S,
    NAMES,
    NO_PAUSE,
    PAUSE_DUE_S,
    PAUSE_EXTERNAL,
    PAUSE_ON_SETPOINT,
    POLL_S,
    PUBLISHED,
    RECALC_S,
    RESTART,
    RESUME_DUE_S,
    RESUME_EXTERNAL,
    RESUME_ON_SETPOINT,
    START_STATE,
    STARTS,
    STOPPED,
    VALVE_CLOSES,
    WAITING_HEAT,
    WEST,
    WINDOW_CLOSES,
    WINDOW_OPENS,
    WITHIN_S,
    Bench,
    Wait,
    boiler_starts,
    calls,
    clock,
    get,
    of,
    pause_mismatches,
    published_at,
    reached,
    seconds,
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
    """East calls with West paused: West resumes, the boiler starts; East watched for a pause."""
    return total(longest(RESUME_EXTERNAL, boiler_starts(role)), NO_PAUSE)


def east_leaves(role: Role) -> Wait:
    """East stops with West calling: the boiler stops at once, West pauses."""
    return longest(VALVE_CLOSES, BOILER_STOPS, PAUSE_EXTERNAL)


def west_leaves(role: Role) -> Wait:
    return longest(RESUME_ON_SETPOINT, BOILER_STOPS)


def both_start(role: Role) -> Wait:
    """West and East start together: the boiler starts, both watched for five calculations."""
    return total(boiler_starts(role), Wait(5 * RECALC_S, 5 * RECALC_S))


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
    author_release: bool  # runs on the author's release too
    start: str  # "fresh" or "learned"
    north: float  # North's setpoint
    measured: bool  # passes on a measurement, once it took place
    trace: bool

    def time_for(self, role: Role) -> Wait:
        return total(START_STATE, self.time(role))

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
    author_release: bool = False,
    start: str = "fresh",
    north: float = CALLING,
    measured: bool = False,
    trace: bool = True,
):
    def register(run: Callable[[Bench], Awaitable[None]]) -> Callable[[Bench], Awaitable[None]]:
        if id in SCENARIOS:
            raise ValueError(f"{id} registered twice")
        if start not in STARTS:
            raise ValueError(f"{id}: start {start!r}")
        timing = time if callable(time) else (lambda _role, w=time: w)
        SCENARIOS[id] = Scenario(id, run, roles, timing, counterpart, minimum, depends, author_release, start, north, measured, trace)
        return run

    return register


# --- BP0: the instance ------------------------------------------------------------------


@scenario(
    "BP0",
    tuple(ROLES),
    Wait(0.0, 3 * 300.0),
    "test_qas_instance.py, test_published_diagnostics_schema.py",
    minimum=True,
    author_release=True,
)
async def BP0(b: Bench) -> None:
    """After the preparation's restart → the instance is read → every SmartPI and VTherm entry is loaded, the only SmartPI entry is its defaults, SmartPI reports no repair, neither integration logs a warning or an error but VTherm's one-time notice that SmartPI was not registered yet, VTherm's central boiler agrees with the test rooms' boiler switch, and each thermostat has SmartPI's diagnostics with learning.enabled on and learning.boiler_paused published (not on the author's release, which has no such field)."""
    for domain in (SMARTPI, VT):
        for entry in await b.api.entries(domain):
            b.check(entry.get("state") == "loaded", f"{domain} {entry['title']!r}: {entry.get('state')}")
    titles = [e["title"] for e in await b.api.entries(SMARTPI)]
    b.check(titles == ["SmartPI defaults"], f"SmartPI entries: {titles}")
    for climate in KEYS:
        took, snap = await b.until(lambda s, c=climate: bool(of(s, c, "phase")), 300.0)
        b.check(took is not None, f"{climate}: SmartPI's diagnostics, phase {of(snap, climate, 'phase')}")
        b.check(of(snap, climate, "enabled") is True, f"{climate}: learning.enabled {of(snap, climate, 'enabled')}")
        if b.author:
            b.note(f"{climate}: the author's {AUTHOR_RELEASE} publishes no learning.boiler_paused ({of(snap, climate, 'paused')})")
        else:
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
    """The start state → West calls alone, below the threshold → West pauses at its next calculation, not at its setpoint's own; VTherm's boiler stays off; East and North (not counted) do not pause; West's valve stays open."""
    await b.west_calls()
    await b.hold()


@scenario("BP2", THREE, lambda role: total(west_alone(role), east_joins(role)), END_TO_END, minimum=True)
async def BP2(b: Bench) -> None:
    """West calls alone and pauses → East calls too, the threshold is reached → West resumes at its next calculation, VTherm's boiler starts (with an activation delay, 120 s later), East does not pause."""
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
    """VTherm's active-device and power sensors disabled, so the threshold cannot be read → West calls alone → West does not pause while its valve is open; once the sensors are back, it pauses."""
    sensors = [b.ids.count, b.ids.power]

    async def disable(by: str | None) -> None:
        for entity_id in sensors:
            await b.r.ha.ws_cmd({"type": "config/entity_registry/update", "entity_id": entity_id, "disabled_by": by})

    try:
        await disable("user")
        took, _ = await b.until(lambda s: s["count"] is None and s["power"] is None, 180.0)
        b.check(took is not None, f"VTherm's sensors gone after {seconds(took)} s: {sensors}")
        b.note("— West calls, the threshold unreadable")
        await b.calls_at(WEST, CALLING)
        seen = await b.watch(WITHIN_S)
        found = [clock(s) for s in seen if of(s, WEST, "paused") is not False]
        b.check(not found, f"West alone, threshold unreadable: not paused for {WITHIN_S:.0f} s ({', '.join(found[:5]) or 'never paused'})")
        # The pause starts only while SmartPI calls: a West not calling would pass too.
        b.check(all(calls(s, WEST) for s in seen[len(seen) // 2 :]), f"West calls meanwhile: valve opening {of(seen[-1], WEST, 'opening')} %")
    finally:
        await disable(None)
        took, snap = await b.until(lambda s: s["count"] is not None and s["power"] is not None, 180.0)
        b.check(took is not None, f"VTherm's sensors back: {snap.get('count')}, {snap.get('power')}")
    if WEST in b.calling:
        took = await b.reach({"West paused": (b.paused_is(WEST, True), PAUSE_EXTERNAL)}, time.time())
        await b.after("the threshold read again")


@scenario("BP5", THREE, lambda role: total(PUBLISHED, west_alone(role), east_joins(role), PUBLISHED), PAUSE_TESTS)
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


@scenario(
    "BP6",
    THREE,
    lambda role: total(west_alone(role), east_joins(role), east_leaves(role), west_leaves(role)),
    PAUSE_TESTS,
)
async def BP6(b: Bench) -> None:
    """West and East call, the boiler on → East stops, the demand drops below the threshold with West heating → VTherm's boiler and its switch go off at once (also with an activation delay), West pauses two calculations later; West stopping then resumes it."""
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


HEAT_UP = Wait(0.0, 3600.0)  # BP7: the calibration's heating step (see its note)
PAUSED_READINGS = Wait(10 * RECALC_S, 10 * RECALC_S)  # BP7: five readings, two calculations apart
NEW_EPISODE = Wait(CYCLE_S / 2, CYCLE_S + MARGIN_S)  # the next cycle's start


async def new_episode_after(b: Bench, climate: str, since: float, label: str) -> dict[str, Any]:
    """The thermostat's next heating starts a new dead-time window after ``since``."""
    took, snap = await b.until(
        lambda s: (published_at(of(s, climate, "episode")) or 0.0) >= since and of(s, climate, "in_window") is True,
        NEW_EPISODE.maximum_s,
    )
    b.check(took is not None, f"{label}: the next heating starts a new dead-time window, episode start {of(snap, climate, 'episode')}, after {datetime.fromtimestamp(since, UTC):%H:%M:%S}")
    return snap


@scenario(
    "BP7",
    THREE,
    lambda role: total(PUBLISHED, HEAT_UP, PAUSE_ON_SETPOINT, PAUSED_READINGS, east_joins(role), NEW_EPISODE),
    PAUSE_TESTS,
    depends="typical when West starts below the calibration's low threshold (setpoint - 0.3 degC), as its setpoint is set 1 degC above the room",
    start="learned",
)
async def BP7(b: Bench) -> None:
    """West learned, its setpoint 1 degC above its room → a forced calibration heats below the threshold → learning pauses: the dead-time detection stays idle and the learned heating dead time unchanged; once East calls, learning resumes and the next heating starts a new dead-time window, its episode starting after the resume."""
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
        b.check(took is not None, f"heating alone below the threshold: paused after {seconds(took)} s")
        for _ in range(5):
            snap = await b.snapshot()
            b.check(
                of(snap, WEST, "dt_state") == "OFF" and of(snap, WEST, "deadtime") == learned,
                f"paused: dead-time detection {of(snap, WEST, 'dt_state')}, heating dead time {of(snap, WEST, 'deadtime')} s (learned {learned} s)",
            )
            await asyncio.sleep(2 * RECALC_S)
        took = await b.east_calls()
        resumed = b.started + (took.get("West resumed") or 0.0) - POLL_S - 5.0  # within a poll
        await new_episode_after(b, WEST, resumed, "after the resume")
    finally:
        # Off and on again cancels the calibration; the runner then puts the start state back.
        await b.hvac(WEST, "off")
        await b.hvac(WEST, "heat")


@scenario("BP8", THREE, both_start, END_TO_END, minimum=True)
async def BP8(b: Bench) -> None:
    """The start state → West and East start calling together → VTherm's boiler starts (with an activation delay, 120 s later) and neither pauses, though VTherm counts their devices a moment after SmartPI first reads the threshold."""

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
    lambda role: total(west_alone(role), RESTART, AFTER_RESTART, PAUSE_EXTERNAL),
    PAUSE_TESTS,
    start="learned",
)
async def BP9(b: Bench) -> None:
    """West learned, calling alone and paused → Home Assistant restarts → once SmartPI runs again, the pause returns within two calculations; nothing is learned in between (accepted updates unchanged, dead-time detection idle, no heating episode from before the restart); a, b and the dead time are kept."""
    await b.west_calls()
    before = kept(await b.snapshot(), WEST)
    restarted_at = await b.restart()
    seen: list[dict[str, Any]] = []

    def watch(s: dict[str, Any]) -> bool:
        seen.append(s)
        return of(s, WEST, "paused") is True

    took, snap = await b.until(watch, PAUSE_EXTERNAL.maximum_s)
    b.check(took is not None, f"the pause returns after {seconds(took)} s")
    for name in ("accepted_a", "accepted_b"):
        values = {of(s, WEST, name) for s in seen if of(s, WEST, name) is not None}
        b.check(values == {before[name]}, f"{name}: {before[name]} before, {sorted(values, key=str)} since")
    b.check(kept(snap, WEST) == before, f"a, b and the dead time kept: {kept(snap, WEST)} (before {before})")
    b.check(of(snap, WEST, "dt_state") == "OFF", f"dead-time detection: {of(snap, WEST, 'dt_state')}")
    start = published_at(of(snap, WEST, "episode"))
    b.check(start is None or start >= restarted_at, f"heating episode: {of(snap, WEST, 'episode')} (restart at {datetime.fromtimestamp(restarted_at, UTC):%H:%M:%S})")
    await b.after("after the restart")


def lowered(role: Role) -> float:
    """BP10: a threshold West alone reaches: 1 device, or 500 W."""
    return 1.0 if role.count_threshold > 0 else 500.0


@scenario(
    "BP10",
    THREE,
    lambda role: total(west_alone(role), longest(RESUME_EXTERNAL, boiler_starts(role)), longest(PAUSE_EXTERNAL, BOILER_STOPS)),
    PAUSE_TESTS,
)
async def BP10(b: Bench) -> None:
    """West calls alone and pauses → VTherm's threshold lowered to 1 device (or 500 W), then back → lowered, West resumes at its next calculation and the boiler starts (with an activation delay, 120 s later), though only West calls; back, West pauses again two calculations later and the boiler stops at once."""
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
    """West calls alone and pauses → West turned off and on, then its window opened and closed → off, its pause ends within a calculation (SmartPI does not call); heating again, the pause returns a calculation later; window open, VTherm turns West off after 30 s and the pause ends; window closed, West heats again and pauses two calculations later."""
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


# --- BP12 (and BP21): a boiler that starts late ----------------------------------------


DEADTIME_MIN_OFF_S = 600.0  # deadtime_estimator.py, min_off_time_seconds
WEST_COOLS = Wait(DEADTIME_MIN_OFF_S, 3600.0)
# The room's dead time as SmartPI detected it without a delay (median 883 s in the check of
# a2b0297), the boiler's delay, and about two minutes of rise that complete the detection.
STEP_DETECTED_S = 883.0 + 120.0


def heat_detection(readings: Sequence[dict[str, Any]]) -> tuple[bool, str | None]:
    """From West's snapshots in order after its start: whether a heating dead-time detection was
    armed, and its outcome once that detection ended (None while it waits, or when none was armed).
    An outcome published before the detection was armed belongs to another one."""
    armed = False
    for s in readings:
        if of(s, WEST, "dt_state") == WAITING_HEAT:
            armed = True
        elif armed:
            return True, of(s, WEST, "heat_outcome")
    return armed, None


def step_detected(role: Role) -> Wait:
    return Wait(role.boiler_delay_s + STEP_DETECTED_S, 3600.0)


def part_b(role: Role) -> Wait:
    return total(west_leaves(role), PUBLISHED, WEST_COOLS, step_detected(role))


async def west_starts_the_boiler(b: Bench, label: str) -> None:
    """BP12 (b), BP21: East calling alone; West stops, its learning is reset, its room cools for
    at least SmartPI's minimum off time before a detection; then West's own start reaches the
    threshold. It records when the boiler starts after West's start and SmartPI's first heating
    dead-time detection; it passes once West's start armed that detection and it ended detected."""
    await b.west_stops()
    await b.api.call(SMARTPI, "reset_smartpi_learning", {"entity_id": WEST})
    took, snap = await b.until(
        lambda s: of(s, WEST, "deadtime_reliable") is False and of(s, WEST, "heat_outcome") is None and not calls(s, WEST),
        MARGIN_S,
    )
    b.check(
        took is not None,
        f"{label} West fresh and off: heating dead time reliable {of(snap, WEST, 'deadtime_reliable')}, last outcome {of(snap, WEST, 'heat_outcome')}",
    )
    off_at = time.time()
    peak: list[float] = []

    def cooled(s: dict[str, Any]) -> bool:
        indoor = of(s, WEST, "indoor")
        if isinstance(indoor, (int, float)):
            peak.append(max(peak[-1], indoor) if peak else indoor)
        return (
            s["t"] - off_at >= DEADTIME_MIN_OFF_S
            and isinstance(indoor, (int, float))
            and peak[-1] - indoor >= 0.1
            and not calls(s, WEST)
            and s["boiler"] == "off"
        )

    took, _ = await b.until(cooled, WEST_COOLS.maximum_s, 10.0)
    b.check(took is not None, f"{label} West off for {seconds(took)} s, its room one sensor step below its peak {peak[-1] if peak else None}, the boiler off")
    if took is None:
        return
    b.note(f"— {label} West's own start")
    await b.calls_at(WEST, CALLING)
    stepped = time.time()
    first_on = boiler_at = None
    paused_seen = False
    readings: list[dict[str, Any]] = []
    armed, outcome = False, None
    snap: dict[str, Any] = {}
    while time.time() - stepped <= step_detected(b.role).maximum_s:
        snap = await b.snapshot()
        if first_on is None and calls(snap, WEST):
            first_on = snap["t"]
        if boiler_at is None and snap["switch"] == "on":
            boiler_at = snap["t"]
        paused_seen = paused_seen or of(snap, WEST, "paused") is True
        readings.append(snap)
        armed, outcome = heat_detection(readings)
        if outcome is not None or (not armed and snap["t"] - stepped > MARGIN_S):
            break
        await asyncio.sleep(10.0)
    b.note(
        f"{label} West's start seen {seconds(first_on and first_on - stepped)} s after its setpoint, the boiler's switch on "
        f"{seconds(first_on and boiler_at and boiler_at - first_on)} s after West's start; paused meanwhile: {paused_seen}"
    )
    b.check(armed, f"{label} West's start armed a heating dead-time detection")
    b.check(outcome == "detected", f"{label} West's heating dead-time detection: {outcome} after {round(time.time() - stepped)} s")
    if outcome == "detected":
        b.note(
            f"{label} first heating dead time {of(snap, WEST, 'deadtime')} s (true 780 s; with the boiler at once BR1's detections "
            f"were 640-1087 s, median 883 s, in the check of a2b0297; with a delay of 120 s BP12 (b) gave 602-1228 s, median 1011 s, on 2ca31af)"
        )


@scenario(
    "BP12",
    ("boiler-delay",),
    lambda role: total(west_alone(role), RESUME_EXTERNAL, Wait(BOILER_DELAY_S, BOILER_DELAY_S + MARGIN_S), NEW_EPISODE, Wait(0.0, MARGIN_S), part_b(role)),
    PAUSE_TESTS,
    start="learned",
    measured=True,
)
async def BP12(b: Bench) -> None:
    """West and East learned, an activation delay of 120 s → (a) West calls alone and pauses, then East calls; (b) West stopped and reset, its room cooling while East calls alone, then West's own start reaches the threshold → (a) West resumes at once while the boiler starts 120 s later; it records that delay, when the next episode starts against the boiler's start, and that no dead-time detection is armed then; (b) no pause, the boiler 120 s after West's start, and SmartPI's first heating dead-time detection, which includes that wait. It passes on the measurement, once West's start armed that detection and it ended detected."""
    await b.west_calls()
    snap = await b.snapshot()
    off_for = snap["t"] - (published_at(snap["boiler_since"]) or snap["t"])
    b.check(off_for >= BOILER_DELAY_S, f"(a) VTherm's boiler off for {off_for:.0f} s before East calls (no activation pending)")
    # E: a "last" value is read against the event that made it: an outcome West already had before
    # the resume is not one of the resume's.
    outcome_before = of(snap, WEST, "heat_outcome")
    took = await b.change(
        "(a) East calls",
        lambda: b.calls_at(EAST, CALLING),
        own=EAST,
        goals={"(a) the boiler's switch on": (lambda s: s["switch"] == "on", Wait(BOILER_DELAY_S, BOILER_DELAY_S + MARGIN_S))},
    )
    resumed, switched = took.get("West resumed"), took.get("(a) the boiler's switch on")
    if resumed is None:
        return
    resumed_at = b.started + resumed
    if switched is not None:
        b.note(f"(a) the boiler's switch on {switched - resumed:.0f} s after West's resume was seen")
    snap = await new_episode_after(b, WEST, resumed_at - POLL_S - 5.0, "(a)")
    episode = published_at(of(snap, WEST, "episode"))
    if episode is not None and switched is not None:
        b.note(f"(a) the dead-time window of the learning starts {b.started + switched - episode:.0f} s before the boiler's switch is seen on")
    outcome = of(snap, WEST, "heat_outcome")
    b.note(
        f"(a) West's dead-time detection: {of(snap, WEST, 'dt_state')}; last outcome {outcome}"
        + (" — from before the resume: no detection ended since" if outcome == outcome_before else " — of a detection that ended since the resume")
    )
    await west_starts_the_boiler(b, "(b)")


# --- BP13-BP20: thresholds of power, partly open valves, restarts --------------------------------


IDLE_PAUSE = Wait(20 * 60.0, 20 * 60.0)  # BP13 A′


def learning_of(snap: dict[str, Any], climate: str) -> dict[str, Any]:
    return {name: of(snap, climate, name) for name in ("accepted_a", "accepted_b", "learn_ok", "deadtime")}


@scenario(
    "BP13",
    LEVELS,
    lambda role: total(west_alone(role), IDLE_PAUSE, east_joins(role), east_leaves(role), west_leaves(role)),
    PAUSE_TESTS + ", test_qas_instance.py",
    start="learned",
)
async def BP13(b: Bench) -> None:
    """West 300 W and East 900 W learned, a power threshold of 600 W → A West calls (300 W), A′ 20 minutes, B East calls (1200 W), C East stops (300 W), D West stops → A: the boiler stays off and West pauses at its next calculation; A′: the pause holds without a break, the boiler never starts, the room cools and nothing is learned; B: the boiler starts (with a delay, 120 s later), West resumes at its next calculation, East never pauses; C: the boiler goes off at once, West pauses two calculations later, and nothing from the gap between them is learned; D: the pause ends."""
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


NATURAL_S = 3 * 3600.0
NATURAL_THRESHOLD_W = 600.0  # West at 2000 W: the boiler runs above a valve opening of 30 %
SETTLE_S = 3600.0  # BP14: the first hour, while the room reaches its setpoint
OVERSHOOT_S = 1800.0


def cycle_metrics(samples: Sequence[dict[str, Any]], setpoint: float, settle_s: float = SETTLE_S) -> dict[str, Any]:
    """BP14's measurement of a trace: VTherm's boiler switches and West's pauses per hour, the
    shortest whole times on and off, the room's swing and its largest error against the setpoint
    after ``settle_s``, its largest overshoot within ``OVERSHOOT_S`` after each boiler start, and
    what West learned (first and last values, detections armed)."""
    if len(samples) < 2:
        return {}
    first, last = samples[0], samples[-1]
    hours = (last["t"] - first["t"]) / 3600.0
    boiler = transitions(samples, "boiler")
    durations: dict[str, list[float]] = {"on": [], "off": []}
    for (t0, value), (t1, _) in zip(boiler[1:], boiler[2:], strict=False):
        if value in durations:
            durations[value].append(t1 - t0)
    settled = [s for s in samples if s["t"] - first["t"] >= settle_s and isinstance(s.get("west.indoor"), (int, float))]
    errors = [s["west.indoor"] - setpoint for s in settled]
    overshoot = 0.0
    for t, value in boiler[1:]:
        if value == "on":
            after = [s["west.indoor"] - setpoint for s in samples if t <= s["t"] <= t + OVERSHOOT_S and isinstance(s.get("west.indoor"), (int, float))]
            overshoot = max([overshoot, *after])

    def ends(key: str) -> tuple[Any, Any]:
        values = [s.get(key) for s in samples if s.get(key) is not None]
        return (values[0], values[-1]) if values else (None, None)

    return {
        "hours": round(hours, 2),
        "boiler_switches_per_hour": round((len(boiler) - 1) / hours, 2) if hours else None,
        "pauses_per_hour": round(sum(1 for _, v in transitions(samples, "west.paused")[1:] if v is True) / hours, 2) if hours else None,
        "shortest_on_s": round(min(durations["on"])) if durations["on"] else None,
        "shortest_off_s": round(min(durations["off"])) if durations["off"] else None,
        "swing_c": round(max(s["west.indoor"] for s in settled) - min(s["west.indoor"] for s in settled), 2) if settled else None,
        "largest_error_c": round(max(errors, key=abs), 2) if errors else None,
        "overshoot_c": round(overshoot, 2),
        "accepted_a": ends("west.accepted_a"),
        "accepted_b": ends("west.accepted_b"),
        "learn_ok": ends("west.learn_ok"),
        "a": ends("west.a"),
        "deadtime": ends("west.deadtime"),
        "detections_armed": sum(1 for _, v in transitions(samples, "west.dt_state")[1:] if v == WAITING_HEAT),
        "detection_outcomes": [v for _, v in transitions(samples, "west.heat_outcome")[1:]],
        "window_outcomes": last.get("west.outcomes"),
    }


def took_place(samples: Sequence[dict[str, Any]], seconds_: float, key: str = "west.opening") -> tuple[bool, str]:
    """A measurement over ``seconds_`` took place: the trace covers it, with readings of ``key`` in
    at least 90 % of its intervals."""
    if not samples:
        return False, "no readings"
    covered = samples[-1]["t"] - samples[0]["t"]
    expected = seconds_ / 5.0
    readings = sum(1 for s in samples if s.get(key) is not None)
    ok = covered >= 0.95 * seconds_ and readings >= 0.9 * expected
    return ok, f"{covered / 60:.0f} of {seconds_ / 60:.0f} min, {readings} readings of {key}"


@scenario(
    "BP14",
    ("boiler-power",),
    Wait(NATURAL_S, NATURAL_S + MARGIN_S),
    PAUSE_TESTS,
    author_release=True,
    start="learned",
    measured=True,
)
async def BP14(b: Bench) -> None:
    """West learned and calling alone (2000 W), VTherm's power threshold at 600 W, West's setpoint just above its room, no other change → 3 hours of its own regulation → it records VTherm's boiler switches and West's pauses per hour, the shortest times on and off, the room's swing around the setpoint, its overshoot after the boiler returns, and what West learned (a, b, dead time, window outcomes, detections); on this version West's pause agrees with its call and the threshold throughout. It passes on the measurement, once the 3 hours were read; the same run on the author's release compares."""
    entity_id, original = await b.threshold_entity()
    try:
        await b.threshold_set(NATURAL_THRESHOLD_W)
        indoor = of(await b.snapshot(), WEST, "indoor") or BASE_SETPOINT
        setpoint = round(indoor + 0.3, 1)
        b.note(f"— West's setpoint {setpoint} degC, its room {indoor} degC; 3 hours")
        await b.setpoints(**{WEST: setpoint})
        begin = len(b.trace.samples) if b.trace else 0
        await asyncio.sleep(NATURAL_S)
        samples = list(b.trace.samples[begin:]) if b.trace else []
        ok, what = took_place(samples, NATURAL_S)
        b.check(ok, f"the measurement took place: {what}")
        for name, value in cycle_metrics(samples, setpoint).items():
            b.note(f"{name}: {value}")
        if not b.author:
            wrong = pause_mismatches(samples, "west")
            b.check(not wrong, f"West's pause agrees with its call and the threshold ({len(wrong)} disagreements: {'; '.join(wrong[:3])})")
    finally:
        await b.api.call("number", "set_value", {"entity_id": entity_id, "value": original})


PARTIAL_S = 45 * 60.0


def held_both(samples: Sequence[dict[str, Any]], due: Callable[[dict[str, Any]], bool], lag_s: float) -> int:
    """How many readings where ``due`` held on every reading of the last ``lag_s``."""
    n = 0
    for i, s in enumerate(samples):
        j = i
        while j >= 0 and s["t"] - samples[j]["t"] <= lag_s and due(samples[j]):
            j -= 1
        if j >= 0 and s["t"] - samples[j]["t"] > lag_s:
            n += 1
    return n


def switches(samples: Sequence[dict[str, Any]], key: str) -> int:
    return max(len(transitions(samples, key)) - 1, 0)


@scenario(
    "BP15",
    ("boiler-power",),
    lambda role: total(west_alone(role), Wait(PARTIAL_S, PARTIAL_S)),
    PAUSE_TESTS,
    start="learned",
    measured=True,
)
async def BP15(b: Bench) -> None:
    """West and East learned, the power threshold of 2500 W → West's valve fully open (2000 W), East's setpoint just above its room so that its valve is partly open (that share of its 2000 W), 45 minutes → both pause while the boiler stands, though both call; both resume once the demand reaches the threshold; it records the switches. It passes on the measurement, once both called below and above the threshold, each long enough for SmartPI to decide."""
    await b.west_calls()
    indoor = of(await b.snapshot(), EAST, "indoor") or BASE_SETPOINT
    b.note(f"— East's setpoint {round(indoor + 0.2, 1)} degC, its room {indoor} degC; 45 minutes")
    await b.setpoints(**{EAST: round(indoor + 0.2, 1)})
    begin = len(b.trace.samples) if b.trace else 0
    await asyncio.sleep(PARTIAL_S)
    samples = list(b.trace.samples[begin:]) if b.trace else []
    both = lambda s: calls(s, WEST) and calls(s, EAST)  # noqa: E731
    below = held_both(samples, lambda s: both(s) and reached(s) is False, PAUSE_DUE_S)
    above = held_both(samples, lambda s: both(s) and reached(s) is True, RESUME_DUE_S)
    b.check(below > 0 and above > 0, f"the measurement took place: both calling below the threshold in {below} readings, above it in {above}")
    for key in ("west", "east"):
        wrong = pause_mismatches(samples, key)
        b.check(not wrong, f"{key}'s pause agrees with its call and the threshold ({len(wrong)} disagreements: {'; '.join(wrong[:3])})")
    openings = [of(s, EAST, "opening") for s in samples if of(s, EAST, "opening") is not None]
    b.note(
        f"switches: boiler {switches(samples, 'boiler')}, West's pause {switches(samples, 'west.paused')}, East's pause "
        f"{switches(samples, 'east.paused')}; East's valve opening {min(openings, default=None)}–{max(openings, default=None)} %"
    )


TAPER_S = 45 * 60.0


@scenario(
    "BP16",
    ("boiler-count", "boiler-power"),
    lambda role: total(west_alone(role), east_joins(role), Wait(TAPER_S, TAPER_S)),
    PAUSE_TESTS,
    start="learned",
    measured=True,
)
async def BP16(b: Bench) -> None:
    """West and East learned, both calling, the boiler on → East's setpoint just above its room, so East comes to it gradually while West calls, 45 minutes → West is paused when and only when the demand is below the threshold (with the device count, an East valve open by any amount keeps it reached; with the power, the opening's share of the device decides); it records East's valve, VTherm's count and power, the boiler, West's pause and what West learned, and the switches. It passes on the measurement, once the 45 minutes were read and East's valve came down from fully open."""
    await b.west_calls()
    await b.east_calls()
    indoor = of(await b.snapshot(), EAST, "indoor") or BASE_SETPOINT
    b.note(f"— East's setpoint {round(indoor + 0.5, 1)} degC, its room {indoor} degC; 45 minutes")
    await b.setpoints(**{EAST: round(indoor + 0.5, 1)})
    b.calling.discard(EAST)
    begin = len(b.trace.samples) if b.trace else 0
    await asyncio.sleep(TAPER_S)
    samples = list(b.trace.samples[begin:]) if b.trace else []
    ok, what = took_place(samples, TAPER_S, "east.opening")
    openings = [of(s, EAST, "opening") for s in samples if of(s, EAST, "opening") is not None]
    tapered = any(0 < c < 100 for c in openings)
    b.check(ok and tapered, f"the measurement took place: {what}; East's valve opening {openings[0] if openings else None} → {openings[-1] if openings else None} % (lowest {min(openings, default=None)})")
    wrong = pause_mismatches(samples, "west")
    b.check(not wrong, f"West's pause agrees with the threshold ({len(wrong)} disagreements: {'; '.join(wrong[:3])})")
    lost = held_both(samples, lambda s: calls(s, WEST) and reached(s) is False, PAUSE_DUE_S)
    valves = [of(s, EAST, "valve") for s in samples if isinstance(of(s, EAST, "valve"), (int, float))]
    first, last = (samples[0], samples[-1]) if samples else ({}, {})
    b.note(
        f"the demand below the threshold with West calling in {lost} readings; East's valve {valves[0] if valves else None} → "
        f"{valves[-1] if valves else None} % (lowest {min(valves, default=None)}); switches: count {switches(samples, 'count')}, "
        f"power {switches(samples, 'power')}, boiler {switches(samples, 'boiler')}, West's pause {switches(samples, 'west.paused')}"
    )
    b.note(f"West's learning: {learning_of(first, WEST)} → {learning_of(last, WEST)}; detections armed {sum(1 for _, v in transitions(samples, 'west.dt_state')[1:] if v == WAITING_HEAT)}")


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


@scenario("BP18", THREE, both_start, END_TO_END)
async def BP18(b: Bench) -> None:
    """The start state → East calls, then West, and West's setpoint changes again at once (two of West's calculations a moment apart at the boiler's start) → neither pauses and the boiler starts (with an activation delay, 120 s later), though the pause's confirmation counts two calculations whatever the time between them."""
    marks: list[float] = []

    async def quick() -> None:
        for climate, value in ((EAST, CALLING), (WEST, CALLING), (WEST, CALLING - 0.5)):
            await b.setpoints(**{climate: value})
            marks.append(time.time())
        b.calling.update(COUNTED)  # at 22.5 degC the valve is fully open too

    await b.change("East, West and West again, at once", quick)
    b.note(f"the three setpoints within {1000 * (marks[-1] - marks[0]):.0f} ms")
    await b.no_pause(COUNTED, 5 * RECALC_S, "the boiler starts")


MODULATE_HOLD = Wait(5 * RECALC_S, 5 * RECALC_S)
STEP_DOWN_C = 0.2
STEP_DOWN_MAX = 10
STEP_DOWN = Wait(4 * RECALC_S, STEP_DOWN_MAX * 2 * RECALC_S + MARGIN_S)


@scenario(
    "BP19",
    ("boiler-count",),
    lambda role: total(PAUSE_ON_SETPOINT, MODULATE_HOLD, STEP_DOWN),
    PAUSE_TESTS,
    depends="the steps down West needs until its valve closes (each 0.2 degC, two calculations)",
    start="learned",
    measured=True,
)
async def BP19(b: Bench) -> None:
    """West learned, its setpoint just above its room, East not calling → West's valve partly open below the threshold, then its setpoint goes down in steps of 0.2 degC (no jump to 7 degC) until the valve closes (0 %) → the pause holds while the valve is open (above 0 %) and ends within the calculation it closes. It passes on the measurement, once West's valve was partly open (above 0 and below 100 %) and then closed."""
    indoor = of(await b.snapshot(), WEST, "indoor") or BASE_SETPOINT
    b.note(f"— West's setpoint {round(indoor + 0.2, 1)} degC, its room {indoor} degC")
    await b.setpoints(**{WEST: round(indoor + 0.2, 1)})
    took, snap = await b.until(b.paused_is(WEST, True), PAUSE_ON_SETPOINT.maximum_s)
    b.check(took is not None, f"West paused after {seconds(took)} s, valve opening {of(snap, WEST, 'opening')} %")
    seen = await b.watch(MODULATE_HOLD.typical_s)
    openings = [of(s, WEST, "opening") for s in seen if of(s, WEST, "opening") is not None]
    b.check(
        bool(openings) and 0 < min(openings) and max(openings) < 100,
        f"the measurement: West's valve partly open, {min(openings, default=None)}–{max(openings, default=None)} %",
    )
    breaks = [clock(s) for s in seen if calls(s, WEST) and of(s, WEST, "paused") is not True]
    b.check(not breaks, f"paused while the valve is open ({', '.join(breaks[:5]) or 'throughout'})")
    setpoint = round(indoor - STEP_DOWN_C, 1)
    closed_at: float | None = None
    resumed_at: float | None = None
    early: list[str] = []
    step = 0
    for step in range(1, STEP_DOWN_MAX + 1):
        b.note(f"— West's setpoint {setpoint} degC")
        await b.setpoints(**{WEST: setpoint})
        for s in await b.watch(2 * RECALC_S):
            if closed_at is None and not calls(s, WEST):
                closed_at = s["t"]
            if resumed_at is None and of(s, WEST, "paused") is False:
                resumed_at = s["t"]
                if closed_at is None:
                    early.append(f"{clock(s)} at {of(s, WEST, 'opening')} %")
        if closed_at is not None and resumed_at is not None:
            break
        setpoint = round(setpoint - STEP_DOWN_C, 1)
    b.check(closed_at is not None, f"the measurement: West's valve closed (0 %) at the setpoint {setpoint} degC, step {step}")
    b.check(not early, f"the pause did not end while the valve was still open ({', '.join(early) or 'no'})")
    b.check(
        closed_at is not None and resumed_at is not None and resumed_at - closed_at <= RECALC_S + POLL_S,
        f"the pause ended {seconds(closed_at and resumed_at and resumed_at - closed_at)} s after the valve closed (within a calculation)",
    )


@scenario(
    "BP20",
    THREE,
    lambda role: total(both_start(role), RESTART, AFTER_RESTART, boiler_starts(role), Wait(5 * RECALC_S, 5 * RECALC_S)),
    PAUSE_TESTS,
    start="learned",
)
async def BP20(b: Bench) -> None:
    """West and East learned, both calling, the boiler on, nothing paused → Home Assistant restarts → once SmartPI runs again, neither pauses while VTherm counts their devices again and the boiler comes back (with an activation delay, 120 s later); a, b and the dead times are kept."""

    async def both() -> None:
        await b.calls_at(WEST, CALLING)
        await b.calls_at(EAST, CALLING)

    await b.change("West and East call", both)
    before = {c: kept(await b.snapshot(), c) for c in COUNTED}
    await b.restart()
    since = time.time()
    seen = await b.watch(5 * RECALC_S)
    found = [f"{NAMES[c]} at {clock(s)}" for s in seen for c in COUNTED if of(s, c, "paused") is True]
    b.check(not found, f"no pause after the restart for {5 * RECALC_S:.0f} s ({', '.join(found[:5]) or 'none seen'})")
    await b.reach({"VTherm's boiler on again": (lambda s: s["boiler"] == "on", boiler_starts(b.role))}, since)
    snap = await b.after("after the restart")
    for climate in COUNTED:
        now = kept(snap, climate)
        b.check(
            all(now[k] == before[climate][k] for k in ("a", "b", "deadtime")) and all((now[k] or 0) >= (before[climate][k] or 0) for k in ("accepted_a", "accepted_b")),
            f"{NAMES[climate]}: a, b and the dead time kept, {now} (before {before[climate]})",
        )


@scenario("BP21", ("boiler-count",), lambda role: total(PUBLISHED, part_b(role)), PAUSE_TESTS, measured=True)
async def BP21(b: Bench) -> None:
    """Fresh, the boiler starting at once (no activation delay) → BP12 (b): East calls alone (paused below the threshold), West stopped and reset, its room cooling, then West's own start reaches the threshold → no pause, the boiler at West's start, and SmartPI's first heating dead-time detection without a delay, the reference for BP12 (b). It passes on the measurement, once West's start armed that detection and it ended detected."""
    await b.change("East calls alone", lambda: b.calls_at(EAST, CALLING), own=EAST)
    await west_starts_the_boiler(b, "(b)")


# --- the reference runs -----------------------------------------------------------------------
#
# The same case on this version and on the author's release, from a fresh start (README.md, section
# 5). Two inputs, each switched by its own pseudo-random binary sequence (IEA EBC Annex 58,
# appendix E, p. 54-55): West's setpoint, and East's call, which starts and stops the boiler. A
# maximum-length sequence of order n repeats after 2**n - 1 steps and holds a level for at most n
# steps. The levels, the steps and the length were chosen in the fork's simulator.

REFERENCE_HOURS = 96
REFERENCE = Wait(REFERENCE_HOURS * 3600.0, REFERENCE_HOURS * 3600.0)
REFERENCE_EVERY_S = 10 * 60.0  # a sample kept in the run's file
SEQUENCE_TAPS = {5: (5, 3), 6: (6, 5)}  # feedback taps of maximum-length sequences
STARTS_FOR_VALIDITY = 6  # SmartPI's dead time is the median of its last 6 detections (technical_doc.md, 3.6)
CHECKPOINT_HOURS = (48, REFERENCE_HOURS)  # run.py --compare


@dataclass(frozen=True)
class Sequence_:
    """A maximum-length pseudo-random binary sequence of ``order`` bits, each held ``step_s``."""

    order: int
    step_s: float
    seed: int

    def bits(self) -> tuple[int, ...]:
        state, out = self.seed, []
        for _ in range(2**self.order - 1):
            out.append(state & 1)
            feedback = 0
            for tap in SEQUENCE_TAPS[self.order]:
                feedback ^= (state >> (self.order - tap)) & 1
            state = (state >> 1) | (feedback << (self.order - 1))
        return tuple(out)

    def high(self, elapsed_s: float) -> bool:
        bits = self.bits()
        return bits[int(elapsed_s // self.step_s) % len(bits)] == 1


WEST_SEQUENCE = Sequence_(order=6, step_s=30 * 60.0, seed=0b000001)  # repeats after 31.5 h
EAST_SEQUENCE = Sequence_(order=5, step_s=40 * 60.0, seed=0b10110)  # repeats after 20.7 h
WEST_LOW, WEST_HIGH = 19.0, 21.0
BOILER_PHASE_S = 3 * 3600.0  # BR2: the boiler off and on in fixed phases


def west_setpoint(elapsed_s: float) -> float:
    return WEST_HIGH if WEST_SEQUENCE.high(elapsed_s) else WEST_LOW


def east_by_sequence(elapsed_s: float) -> float:
    return CALLING if EAST_SEQUENCE.high(elapsed_s) else STOPPED


def east_by_phases(elapsed_s: float) -> float:
    """Off first, then on, each for ``BOILER_PHASE_S``."""
    return CALLING if int(elapsed_s // BOILER_PHASE_S) % 2 == 1 else STOPPED


def reference_truth() -> dict[str, float]:
    """West's true values (pack.py): a, b, and its dead time between the radiator's delay and its
    delay plus lag."""
    west = room("West")
    return {
        "a": west.gain,
        "b": west.loss,
        "deadtime_delay_s": west.delay_min * 60.0,
        "deadtime_delay_lag_s": (west.delay_min + west.lag_min) * 60.0,
    }


def _reference_sample(data: dict[str, Any], boiler_state: Any, elapsed_s: float, wanted: dict[str, float]) -> dict[str, Any]:
    return {
        "at": datetime.now(UTC).isoformat(),
        "hours": round(elapsed_s / 3600.0, 3),
        "west_setpoint": wanted[WEST],
        "east_setpoint": wanted[EAST],
        "control_phase": get(data, "control.phase"),
        "deadtime_heat_s": get(data, "model.deadtime_heat_s"),
        "deadtime_heat_reliable": get(data, "model.deadtime_heat_reliable"),
        "a": get(data, "model.a"),
        "b": get(data, "model.b"),
        "accepted_updates_a": get(data, "learning.accepted_updates_a"),
        "accepted_updates_b": get(data, "learning.accepted_updates_b"),
        "boiler_paused": get(data, "learning.boiler_paused"),
        "boiler": boiler_state,
        "indoor": get(data, "temperature.indoor"),
        "valve_opening": get(data, "power.current_cycle_percent"),
    }


def _reference_file(b: Bench, sid: str) -> pathlib.Path:
    from ha_client import N  # noqa: PLC0415 — the bench's client
    from harness import results_file  # noqa: PLC0415 — the bench's harness

    version = f"author-{AUTHOR_RELEASE}" if b.author else "qas"
    return results_file(b.r.pack).parent / f"{sid}-{version}-instance{N}.json"


async def _reference_run(b: Bench, sid: str, east: Callable[[float], float]) -> None:
    """West from a fresh start for ``REFERENCE`` hours, its setpoint by ``WEST_SEQUENCE`` and East's
    by ``east``; West's model kept every ``REFERENCE_EVERY_S`` in the run's file."""
    r = b.r
    if b.ids.diag[WEST] is None or b.ids.boiler is None:
        b.check(False, f"West's diagnostics {b.ids.diag[WEST]}, VTherm's boiler {b.ids.boiler}")
        return
    await b.api.call(SMARTPI, "reset_smartpi_learning", {"entity_id": WEST})
    took, snap = await b.until(
        lambda s: of(s, WEST, "phase") == "Hysteresis" and of(s, WEST, "deadtime_reliable") is False and of(s, WEST, "accepted_a") == 0,
        MARGIN_S,
    )
    b.check(
        took is not None,
        f"West starts fresh: phase {of(snap, WEST, 'phase')}, heating dead time reliable {of(snap, WEST, 'deadtime_reliable')}, "
        f"accepted updates of a {of(snap, WEST, 'accepted_a')}",
    )
    out = _reference_file(b, sid)
    version = f"author {AUTHOR_RELEASE}" if b.author else "qas"
    samples: list[dict[str, Any]] = []
    starts = {"boiler_off": 0, "boiler_on": 0}
    alone = {"readings": 0, "boiler_off": 0, "paused": 0}
    set_to: dict[str, float] = {}
    east_stopped_at: float | None = None
    calling_readings = 0
    started = time.time()
    next_sample_s = REFERENCE_EVERY_S
    while (elapsed := time.time() - started) < REFERENCE.typical_s:
        wanted = {WEST: west_setpoint(elapsed), EAST: east(elapsed)}
        changed = {climate: value for climate, value in wanted.items() if set_to.get(climate) != value}
        if changed:
            await b.setpoints(**changed)
            set_to.update(changed)
            if EAST in changed:
                east_stopped_at = elapsed if changed[EAST] == STOPPED else None
        data = await b.live(WEST)
        state = await r.ha.state(b.ids.boiler)
        boiler_state = state["state"] if state else None
        if (get(data, "power.current_cycle_percent") or 0) > 0:
            if calling_readings == 0:
                starts["boiler_on" if boiler_state == "on" else "boiler_off"] += 1
            calling_readings += 1
        else:
            calling_readings = 0
        # West calling alone for three readings (its pause comes at the second calculation), East
        # stopped for longer than VTherm takes to stop the boiler.
        if calling_readings >= 3 and east_stopped_at is not None and elapsed - east_stopped_at > WITHIN_S:
            alone["readings"] += 1
            alone["boiler_off"] += boiler_state == "off"
            alone["paused"] += get(data, "learning.boiler_paused") is True
        if elapsed >= next_sample_s:
            samples.append(_reference_sample(data, boiler_state, elapsed, wanted))
            next_sample_s += REFERENCE_EVERY_S
            out.write_text(
                json.dumps({"scenario": sid, "version": version, "truth": reference_truth(), "starts": starts, "samples": samples}, indent=1),
                encoding="utf-8",
            )
        await asyncio.sleep(RECALC_S)
    b.note(f"kept: {out}")
    b.check(
        alone["readings"] > 0 and alone["boiler_off"] == alone["readings"],
        f"West calling alone: VTherm's boiler off in {alone['boiler_off']} of {alone['readings']} readings",
    )
    if b.author:
        b.note(f"the author's {AUTHOR_RELEASE} publishes no learning.boiler_paused")
    else:
        b.check(alone["paused"] == alone["readings"], f"West calling alone: learning paused in {alone['paused']} of {alone['readings']} readings")
    b.note(f"West's heating starts: {starts['boiler_off']} with the boiler off, {starts['boiler_on']} with it on")
    for hours in CHECKPOINT_HOURS:
        at = [s for s in samples if s["hours"] <= hours + 0.01]
        if at:
            s = at[-1]
            b.note(
                f"at {s['hours']:g} h: heating dead time {s['deadtime_heat_s']} s (reliable {s['deadtime_heat_reliable']}), "
                f"phase {s['control_phase']}, a {s['a']}, b {s['b']}"
            )


@scenario("BR1", ("boiler-count", "boiler-power"), total(PUBLISHED, REFERENCE), PAUSE_TESTS, author_release=True, north=BASE_SETPOINT, trace=False)
async def BR1(b: Bench) -> None:
    """Fresh, North at 20 degC → reference run, the same on this version and on the author's release: for 96 hours West's setpoint switches between 19 and 21 degC and East's call (so the boiler) on and off, each by its own pseudo-random sequence (steps of 30 and 40 minutes) → every 10 minutes West's dead time, a and b are kept (BR1-<version>-instance<N>.json in the results), and run.py --compare judges both versions' heating dead time at 48 hours and at the end against the room's true value."""
    await _reference_run(b, "BR1", east_by_sequence)


@scenario("BR2", ("boiler-count",), total(PUBLISHED, REFERENCE), PAUSE_TESTS, author_release=True, north=BASE_SETPOINT, measured=True, trace=False)
async def BR2(b: Bench) -> None:
    """Fresh, North at 20 degC → as BR1, but East calls in fixed phases of 3 hours, off first, so West mostly starts heating while the boiler is off → it measures a limit of the pause, the same on this version and on the author's release: kept as BR2-<version>-instance<N>.json and shown by run.py --compare, not judged. It passes on the measurement, once the 96 hours ran."""
    await _reference_run(b, "BR2", east_by_phases)
