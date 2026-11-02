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
what that is (BP12, BR2).

BR1 and BR2 are the reference runs: the same case on this version and on the author's release
(``pack.AUTHOR_RELEASE``), which has no learning pause. On it the runner runs with
``--author-release``: only BP0, BR1 and BR2, and they do not look for
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
    NO_PAUSE,
    PAUSE_EXTERNAL,
    PAUSE_ON_SETPOINT,
    POLL_S,
    PUBLISHED,
    RECALC_S,
    RESTART,
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
    published_at,
    seconds,
    total,
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


# --- BP0: the instance --------------------------------------------------------------------------


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


# --- BP1-BP11: the pause, step by step -------------------------------------------------------------


@scenario("BP1", THREE, lambda role: total(west_alone(role), HOLD), END_TO_END, minimum=True)
async def BP1(b: Bench) -> None:
    """The start state → West calls alone, below the threshold → West pauses at its next calculation, not at its setpoint's own; VTherm's boiler stays off; East and North (not counted) do not pause; West's command goes on."""
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
    """VTherm's active-device and power sensors disabled, so the threshold cannot be read → West calls alone → West does not pause while its command is above 0; once the sensors are back, it pauses."""
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
        b.check(all(calls(s, WEST) for s in seen[len(seen) // 2 :]), f"West calls meanwhile: command {of(seen[-1], WEST, 'command')} %")
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
            "East's valve closed": (lambda s: of(s, EAST, "device") == 0.0, VALVE_CLOSES),
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
        b.calling.add(WEST)  # the heating step calls at full power
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


# --- BP12: a boiler that starts late -------------------------------------------------


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
    """BP12 (b): East calling alone; West stops, its learning is reset, its room cools for
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
        "command": get(data, "power.current_cycle_percent"),
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
